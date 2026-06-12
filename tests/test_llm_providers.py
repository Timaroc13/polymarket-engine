"""Unit tests for LLM provider implementations and get_provider_from_env()."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from crypto_news_parser.llm_adapter import (
    ClaudeHaikuSignalProvider,
    GeminiFlashSignalProvider,
    NoopLLMProvider,
    RefinementRequest,
    get_provider_from_env,
)
from crypto_news_parser.models import EventType

# ---------------------------------------------------------------------------
# get_provider_from_env()
# ---------------------------------------------------------------------------


def test_no_provider_when_llm_disabled(monkeypatch):
    monkeypatch.delenv("LLM_ENABLE", raising=False)
    assert get_provider_from_env() is None


def test_gemini_provider_selected_by_default(monkeypatch):
    monkeypatch.setenv("LLM_ENABLE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    provider = get_provider_from_env()
    assert isinstance(provider, GeminiFlashSignalProvider)
    assert provider.name == "gemini-flash"


def test_anthropic_provider_selected_explicitly(monkeypatch):
    monkeypatch.setenv("LLM_ENABLE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")
    provider = get_provider_from_env()
    assert isinstance(provider, ClaudeHaikuSignalProvider)
    assert provider.name == "claude-haiku"


def test_noop_when_gemini_key_missing(monkeypatch):
    monkeypatch.setenv("LLM_ENABLE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = get_provider_from_env()
    assert isinstance(provider, NoopLLMProvider)


def test_noop_when_anthropic_key_missing(monkeypatch):
    monkeypatch.setenv("LLM_ENABLE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = get_provider_from_env()
    assert isinstance(provider, NoopLLMProvider)


# ---------------------------------------------------------------------------
# GeminiFlashSignalProvider.estimate_p_model()
# ---------------------------------------------------------------------------


def _gemini_response(p_model: float) -> MagicMock:
    body = {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({"p_model": p_model, "reasoning": "test"})}]}
        }]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(body).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


@pytest.mark.asyncio
async def test_gemini_returns_valid_p_model():
    provider = GeminiFlashSignalProvider(api_key="fake")
    with patch("urllib.request.urlopen", return_value=_gemini_response(0.68)):
        result = await provider.estimate_p_model("Will BTC reach $100k?", "BTC is surging.")
    assert result == 0.68


@pytest.mark.asyncio
async def test_gemini_returns_none_on_network_error():
    provider = GeminiFlashSignalProvider(api_key="fake")
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = await provider.estimate_p_model("question", "text")
    assert result is None


@pytest.mark.asyncio
async def test_gemini_returns_none_on_invalid_json():
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"not json"
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    provider = GeminiFlashSignalProvider(api_key="fake")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = await provider.estimate_p_model("question", "text")
    assert result is None


@pytest.mark.asyncio
async def test_gemini_returns_none_on_out_of_range():
    provider = GeminiFlashSignalProvider(api_key="fake")
    with patch("urllib.request.urlopen", return_value=_gemini_response(1.5)):
        result = await provider.estimate_p_model("question", "text")
    assert result is None


# ---------------------------------------------------------------------------
# ClaudeHaikuSignalProvider.estimate_p_model()
# ---------------------------------------------------------------------------


def _claude_response(p_model: float) -> MagicMock:
    body = {
        "content": [{"text": json.dumps({"p_model": p_model, "reasoning": "test"})}]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(body).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


@pytest.mark.asyncio
async def test_claude_returns_valid_p_model():
    provider = ClaudeHaikuSignalProvider(api_key="fake")
    with patch("urllib.request.urlopen", return_value=_claude_response(0.72)):
        result = await provider.estimate_p_model("Will BTC reach $100k?", "BTC surging.")
    assert result == 0.72


@pytest.mark.asyncio
async def test_claude_returns_none_on_network_error():
    provider = ClaudeHaikuSignalProvider(api_key="fake")
    with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        result = await provider.estimate_p_model("question", "text")
    assert result is None


# ---------------------------------------------------------------------------
# NoopLLMProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_estimate_p_model_always_returns_none():
    provider = NoopLLMProvider()
    result = await provider.estimate_p_model("any question", "any text")
    assert result is None


# ---------------------------------------------------------------------------
# GeminiFlashSignalProvider.refine()
# ---------------------------------------------------------------------------


def _make_refine_request(
    text: str = "SEC sues Binance for securities violations.",
    heuristic_event_type: EventType = EventType.UNKNOWN,
    heuristic_confidence: float = 0.4,
    heuristic_assets: tuple = ("BTC",),
    heuristic_entities: tuple = (),
) -> RefinementRequest:
    return RefinementRequest(
        text=text,
        heuristic_event_type=heuristic_event_type,
        heuristic_confidence=heuristic_confidence,
        heuristic_assets=heuristic_assets,
        heuristic_entities=heuristic_entities,
    )


def _gemini_refine_response(event_type: str, assets: list, entities: list) -> MagicMock:
    body = {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({
                "event_type": event_type,
                "assets": assets,
                "entities": entities,
            })}]}
        }]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(body).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


@pytest.mark.asyncio
async def test_gemini_refine_returns_valid_event_type_and_assets():
    provider = GeminiFlashSignalProvider(api_key="fake")
    mock_resp = _gemini_refine_response(
        event_type="REGULATORY_ACTION_ENFORCEMENT",
        assets=["BNB", "BTC"],
        entities=["SEC", "Binance"],
    )
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = await provider.refine(_make_refine_request())
    assert result.event_type == EventType.REGULATORY_ACTION_ENFORCEMENT
    assert result.assets == ["BNB", "BTC"]
    assert result.entities == ["SEC", "Binance"]


@pytest.mark.asyncio
async def test_gemini_refine_returns_empty_on_network_error():
    provider = GeminiFlashSignalProvider(api_key="fake")
    with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        result = await provider.refine(_make_refine_request())
    assert result.event_type is None
    assert result.assets is None


@pytest.mark.asyncio
async def test_gemini_refine_returns_empty_on_invalid_json():
    body = {"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(body).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    provider = GeminiFlashSignalProvider(api_key="fake")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = await provider.refine(_make_refine_request())
    assert result.event_type is None


@pytest.mark.asyncio
async def test_gemini_refine_ignores_invalid_event_type_value():
    """If the LLM returns an unknown event_type string, event_type should be None."""
    mock_resp = _gemini_refine_response(
        event_type="TOTALLY_MADE_UP_TYPE",
        assets=["ETH"],
        entities=[],
    )
    provider = GeminiFlashSignalProvider(api_key="fake")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = await provider.refine(_make_refine_request())
    assert result.event_type is None
    assert result.assets == ["ETH"]


@pytest.mark.asyncio
async def test_noop_refine_always_returns_empty():
    provider = NoopLLMProvider()
    result = await provider.refine(_make_refine_request())
    assert result.event_type is None
    assert result.assets is None
