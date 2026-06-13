"""Tests for the in-process scheduler (app-scheduler capability)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import crypto_news_parser.scheduler as sched
from crypto_news_parser.main import app
from crypto_news_parser.models import (
    FlowMarketResult,
    FlowScanResponse,
)


def _result(tier: str, question: str = "Will it?") -> FlowMarketResult:
    return FlowMarketResult(
        market_id=f"cond-{tier.lower()}",
        market_question=question,
        signal_score={"LOW": 10, "MEDIUM": 50, "HIGH": 80}[tier],
        risk_tier=tier,
        dominant_side="YES" if tier != "LOW" else None,
        dominant_side_usdc=25_000.0,
        dominant_side_count=12,
        dominant_side_count_pct=0.9,
        new_wallet_count_yes=12,
        new_wallet_count_no=1,
        new_wallet_usdc_yes=25_000.0,
        new_wallet_usdc_no=500.0,
        new_wallet_count_total=13,
        new_wallet_total_usdc=25_500.0,
        recent_burst_pct=0.65,
        p_market_at_scan=0.61,
        days_to_resolution=3,
        new_wallets=[],
        summary="test",
    )


def _scan_fn(*tiers: str):
    async def fn(req):
        return FlowScanResponse(
            results=[_result(t) for t in tiers], scanned=len(tiers), stored=False
        )
    return fn


# ---------------------------------------------------------------------------
# Alert formatting and tier filtering
# ---------------------------------------------------------------------------


def test_format_alert_contents():
    text = sched.format_alert(_result("HIGH").model_dump())
    assert "HIGH informed-flow signal" in text
    assert "Will it?" in text
    assert "side: YES | score: 80" in text
    assert "61%" in text
    assert "3 days" in text


def test_format_alert_handles_missing_price():
    r = _result("HIGH").model_dump()
    r["p_market_at_scan"] = None
    assert "market YES price at scan: ?" in sched.format_alert(r)


@pytest.mark.asyncio
async def test_scan_once_alerts_only_high_by_default(monkeypatch):
    sent: list[str] = []

    async def fake_send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(sched, "send_telegram", fake_send)
    alerts = await sched.scan_once(_scan_fn("HIGH", "LOW", "MEDIUM"))
    assert alerts == 1
    assert len(sent) == 1
    assert "HIGH" in sent[0]


@pytest.mark.asyncio
async def test_scan_once_respects_alert_min_tier(monkeypatch):
    sent: list[str] = []

    async def fake_send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(sched, "send_telegram", fake_send)
    monkeypatch.setenv("ALERT_MIN_TIER", "MEDIUM")
    alerts = await sched.scan_once(_scan_fn("HIGH", "LOW", "MEDIUM"))
    assert alerts == 2


@pytest.mark.asyncio
async def test_scan_once_uses_env_scan_params(monkeypatch):
    captured = {}

    async def fn(req):
        captured["top_n"] = req.top_n
        captured["max_wallets"] = req.max_wallets
        return FlowScanResponse(results=[], scanned=0, stored=False)

    monkeypatch.setenv("SCAN_TOP_N", "7")
    monkeypatch.setenv("SCAN_MAX_WALLETS", "150")
    await sched.scan_once(fn)
    assert captured == {"top_n": 7, "max_wallets": 150}


def test_telegram_noop_without_config(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert sched._send_telegram_sync("hello") is False


@pytest.mark.asyncio
async def test_poll_once_skips_without_persistence(monkeypatch):
    monkeypatch.delenv("ENABLE_PERSISTENCE", raising=False)
    called = []

    async def poll_fn():
        called.append(1)

    assert await sched.poll_once(poll_fn) == 0
    assert called == []


# ---------------------------------------------------------------------------
# Loop resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_survives_failing_cycle():
    calls = []
    done = asyncio.Event()

    async def cycle():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        done.set()

    task = asyncio.create_task(sched._loop("test", 0.01, cycle))
    await asyncio.wait_for(done.wait(), timeout=2)
    task.cancel()
    assert len(calls) >= 2  # cycle ran again after the failure


# ---------------------------------------------------------------------------
# Lifespan integration
# ---------------------------------------------------------------------------


def test_lifespan_starts_tasks_when_enabled(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLE", "1")
    # Long intervals + startup delay: tasks spawn but no cycle executes mid-test.
    monkeypatch.setenv("SCAN_INTERVAL_HOURS", "24")
    monkeypatch.setenv("POLL_INTERVAL_MINUTES", "1440")
    monkeypatch.delenv("ENABLE_PERSISTENCE", raising=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert len(app.state.scheduler_tasks) == 2
        assert all(not t.done() for t in app.state.scheduler_tasks)
    assert all(t.cancelled() or t.done() for t in app.state.scheduler_tasks)


def test_lifespan_no_tasks_by_default(monkeypatch):
    monkeypatch.delenv("SCHEDULER_ENABLE", raising=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert app.state.scheduler_tasks == []
