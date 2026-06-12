from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, field_validator

from .models import EventType

PROMPT_VERSION = "refine-v1-2026-01-29"
SIGNAL_PROMPT_VERSION = "signal-v1-2026-03-10"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefinementRequest:
    text: str
    heuristic_event_type: EventType
    heuristic_confidence: float
    heuristic_assets: tuple[str, ...]
    heuristic_entities: tuple[str, ...]
    prompt_version: str = PROMPT_VERSION
    deterministic: bool = False
    seed: int | None = None


class LLMRefinement(BaseModel):
    event_type: EventType | None = None
    assets: list[str] | None = None
    entities: list[str] | None = None

    @field_validator("assets", "entities")
    @classmethod
    def _normalize_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            s = item.strip()
            if s:
                cleaned.append(s)
        return cleaned


class LLMProvider(Protocol):
    name: str
    supports_determinism: bool

    async def refine(self, request: RefinementRequest) -> LLMRefinement:  # pragma: no cover
        ...

    async def estimate_p_model(
        self, market_question: str, text: str
    ) -> float | None:  # pragma: no cover
        """Estimate the true probability for a prediction market question.

        Returns a float in [0.0, 1.0] on success, or None to signal fallback to heuristic.
        """
        ...


class NoopLLMProvider:
    name = "none"
    supports_determinism = True

    async def refine(self, request: RefinementRequest) -> LLMRefinement:
        _ = request
        return LLMRefinement()

    async def estimate_p_model(self, market_question: str, text: str) -> float | None:
        return None


class ClaudeHaikuSignalProvider:
    """LLM provider that calls Claude Haiku to estimate prediction market probabilities."""

    name = "claude-haiku"
    supports_determinism = False
    _MODEL = "claude-haiku-4-5-20251001"
    _API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def refine(self, request: RefinementRequest) -> LLMRefinement:
        return LLMRefinement()

    async def estimate_p_model(self, market_question: str, text: str) -> float | None:
        prompt = (
            f"You are a prediction market analyst. Given the following prediction market question "
            f"and recent news, estimate the probability that the question resolves YES.\n\n"
            f"Market question: {market_question}\n\n"
            f"Recent news:\n{text[:1500]}\n\n"
            f"Reply with ONLY a JSON object, no other text: "
            f'{{"p_model": 0.XX, "reasoning": "one sentence"}}'
        )
        payload = json.dumps({
            "model": self._MODEL,
            "max_tokens": 256,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            self._API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = body["content"][0]["text"].strip()
            parsed = json.loads(content)
            p = float(parsed["p_model"])
            if not (0.0 <= p <= 1.0):
                raise ValueError(f"p_model out of range: {p}")
            return p
        except Exception as exc:
            logger.warning("ClaudeHaikuSignalProvider.estimate_p_model failed: %s", exc)
            return None


class GeminiFlashSignalProvider:
    """LLM provider using Google Gemini Flash (free tier) to estimate
    prediction market probabilities."""

    name = "gemini-flash"
    supports_determinism = False
    _MODEL = "gemini-flash-latest"
    _API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def refine(self, request: RefinementRequest) -> LLMRefinement:
        valid_event_types = ", ".join(e.value for e in EventType)
        prompt = (
            f"You are a crypto-news classifier. Given the news text below, correct the event_type "
            f"and assets list if they are wrong or incomplete.\n\n"
            f"News text:\n{request.text[:2000]}\n\n"
            f"Heuristic result (may be wrong):\n"
            f"  event_type: {request.heuristic_event_type.value}\n"
            f"  confidence: {request.heuristic_confidence:.2f}\n"
            f"  assets: {list(request.heuristic_assets)}\n"
            f"  entities: {list(request.heuristic_entities)}\n\n"
            f"Valid event_type values: {valid_event_types}\n\n"
            f"Reply with ONLY a JSON object, no other text:\n"
            f'{{"event_type": "CORRECT_EVENT_TYPE", "assets": ["BTC"], '
            f'"entities": ["Some Entity"]}}'
        )
        payload = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 512, "temperature": 0.0},
        }).encode("utf-8")
        url = f"{self._API_URL}?key={self._api_key}"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                raw = " ".join(
                    p["text"] for p in body["candidates"][0]["content"]["parts"]
                    if "text" in p
                ).strip()
                start = raw.index("{")
                end = raw.rindex("}") + 1
                parsed = json.loads(raw[start:end])
                event_type: EventType | None = None
                if "event_type" in parsed:
                    try:
                        event_type = EventType(parsed["event_type"])
                    except ValueError:
                        event_type = None
                assets = parsed.get("assets") if isinstance(parsed.get("assets"), list) else None
                entities = (
                    parsed.get("entities") if isinstance(parsed.get("entities"), list) else None
                )
                return LLMRefinement(event_type=event_type, assets=assets, entities=entities)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503):
                    body_text = exc.read().decode("utf-8", errors="ignore")
                    m = re.search(r"retry in (\d+(?:\.\d+)?)s", body_text)
                    wait = float(m.group(1)) + 1 if m else 5
                    wait = min(wait, 30)
                    logger.warning(
                        "GeminiFlashSignalProvider.refine: %d, retrying in %.0fs"
                        " (attempt %d/3)",
                        exc.code, wait, attempt + 1,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.warning("GeminiFlashSignalProvider.refine failed: %s", exc)
                    return LLMRefinement()
            except Exception as exc:
                logger.warning("GeminiFlashSignalProvider.refine failed: %s", exc)
                return LLMRefinement()
        logger.warning("GeminiFlashSignalProvider.refine: exhausted retries, using heuristic")
        return LLMRefinement()

    async def estimate_p_model(self, market_question: str, text: str) -> float | None:
        prompt = (
            f"You are a prediction market analyst. Given the following prediction market question "
            f"and recent news, estimate the probability that the question resolves YES.\n\n"
            f"Market question: {market_question}\n\n"
            f"Recent news:\n{text[:1500]}\n\n"
            f"Reply with ONLY a JSON object, no other text: "
            f'{{"p_model": 0.XX, "reasoning": "one sentence"}}'
        )
        payload = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.1},
        }).encode("utf-8")
        url = f"{self._API_URL}?key={self._api_key}"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                raw = " ".join(
                    p["text"] for p in body["candidates"][0]["content"]["parts"]
                    if "text" in p
                ).strip()
                # Extract the outermost JSON object robustly (handles markdown fences)
                start = raw.index("{")
                end = raw.rindex("}") + 1
                parsed = json.loads(raw[start:end])
                p = float(parsed["p_model"])
                if not (0.0 <= p <= 1.0):
                    raise ValueError(f"p_model out of range: {p}")
                return p
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503):
                    body_text = exc.read().decode("utf-8", errors="ignore")
                    m = re.search(r"retry in (\d+(?:\.\d+)?)s", body_text)
                    wait = float(m.group(1)) + 1 if m else 5
                    wait = min(wait, 30)
                    logger.warning(
                        "GeminiFlashSignalProvider: %d, retrying in %.0fs (attempt %d/3)",
                        exc.code, wait, attempt + 1,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.warning("GeminiFlashSignalProvider.estimate_p_model failed: %s", exc)
                    return None
            except Exception as exc:
                logger.warning("GeminiFlashSignalProvider.estimate_p_model failed: %s", exc)
                return None
        logger.warning("GeminiFlashSignalProvider.estimate_p_model: exhausted retries")
        return None


def stable_seed(text: str, prompt_version: str = PROMPT_VERSION) -> int:
    """Deterministic seed derived from input text + prompt version."""

    payload = f"{prompt_version}\n{text}".encode("utf-8", errors="ignore")
    return int(zlib.crc32(payload) & 0xFFFFFFFF)


def get_provider_from_env() -> LLMProvider | None:
    """Returns an LLM provider if configured.

    Environment variables:
      LLM_ENABLE=1          Enable LLM calls (default: disabled).
      LLM_PROVIDER          Which provider to use: "gemini" (default) | "anthropic"
      GEMINI_API_KEY        Required when LLM_PROVIDER=gemini (free tier: 1M tokens/day).
      ANTHROPIC_API_KEY     Required when LLM_PROVIDER=anthropic.

    By default, no provider is enabled and no network calls are made.
    """

    enabled = os.getenv("LLM_ENABLE", "").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return None

    provider_name = os.getenv("LLM_PROVIDER", "gemini").strip().lower()

    if provider_name == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            logger.warning(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set"
                " — falling back to heuristic"
            )
            return NoopLLMProvider()
        return ClaudeHaikuSignalProvider(api_key=api_key)

    # Default: gemini
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set"
            " — falling back to heuristic"
        )
        return NoopLLMProvider()
    return GeminiFlashSignalProvider(api_key=api_key)
