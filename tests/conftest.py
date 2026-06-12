"""Shared test configuration."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_llm_calls(monkeypatch):
    """Keep tests hermetic: a local .env with LLM_ENABLE=1 must not cause real
    LLM API calls (slow, rate-limited, non-deterministic). Tests that exercise
    the LLM path mock get_llm_provider explicitly."""
    monkeypatch.delenv("LLM_ENABLE", raising=False)
