"""Tests for the paper-trading replay (paper-trading capability)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from crypto_news_parser.main import app
from crypto_news_parser.paper import simulate_paper_trading
from crypto_news_parser.storage import (
    get_paper_entries,
    mark_market_resolved,
    store_flow_scan,
    track_market_if_new,
)

client = TestClient(app)


def _entry(win, price, tier="HIGH", ts=1_750_000_000):
    return {"win": win, "price": price, "tier": tier, "resolved_at": ts}


# ---------------------------------------------------------------------------
# Simulation math (spec scenarios)
# ---------------------------------------------------------------------------


def test_win_at_even_odds_no_fee():
    r = simulate_paper_trading([_entry(1, 0.5)], stake=100, fee=0.0)
    assert r["overall"]["pnl"] == 100.0
    assert r["overall"]["roi"] == 1.0


def test_fee_haircut_on_winnings():
    r = simulate_paper_trading([_entry(1, 0.5)], stake=100, fee=0.02)
    assert r["overall"]["pnl"] == 98.0


def test_loss_costs_stake_regardless_of_price():
    r = simulate_paper_trading([_entry(0, 0.9)], stake=100, fee=0.02)
    assert r["overall"]["pnl"] == -100.0
    assert r["overall"]["win_rate"] == 0.0


def test_unusable_price_excluded():
    r = simulate_paper_trading([_entry(1, 0.0), _entry(1, 1.0)], stake=100)
    assert r["overall"]["trades"] == 0
    assert r["curve"] == []


def test_longshot_win_pays_odds():
    # price 0.2 → profit = 100 * 4 * 0.98 = 392
    r = simulate_paper_trading([_entry(1, 0.2)], stake=100, fee=0.02)
    assert abs(r["overall"]["pnl"] - 392.0) < 1e-9


def test_tier_buckets_and_curve():
    entries = [_entry(1, 0.5, "HIGH"), _entry(0, 0.5, "LOW"), _entry(1, 0.5, "HIGH")]
    r = simulate_paper_trading(entries, stake=100, fee=0.0)
    assert r["tiers"]["HIGH"]["trades"] == 2
    assert r["tiers"]["HIGH"]["pnl"] == 200.0
    assert r["tiers"]["LOW"]["pnl"] == -100.0
    assert len(r["curve"]) == 3
    assert r["curve"][-1]["equity"] == 100.0
    assert r["curve"][-1]["equity_high"] == 200.0


def test_max_drawdown():
    # +100 (peak 100), -100 (equity 0, dd 100), -100 (equity -100, dd 200)
    entries = [_entry(1, 0.5), _entry(0, 0.5), _entry(0, 0.5)]
    r = simulate_paper_trading(entries, stake=100, fee=0.0)
    assert r["max_drawdown"] == 200.0


# ---------------------------------------------------------------------------
# Storage entries + dashboard payload
# ---------------------------------------------------------------------------


def _scan(condition_id, dominant="YES", p_market=0.5, tier="HIGH"):
    return {
        "market_id": condition_id,
        "market_question": f"Q {condition_id}",
        "signal_score": 75,
        "risk_tier": tier,
        "dominant_side": dominant,
        "dominant_side_usdc": 10_000.0,
        "p_market_at_scan": p_market,
    }


def test_get_paper_entries_dominant_side_pricing(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    # Dominant NO at YES-price 0.7 → entry price 0.3; resolved No → win
    store_flow_scan(result=_scan("cond-no", dominant="NO", p_market=0.7))
    track_market_if_new(condition_id="cond-no")
    mark_market_resolved(condition_id="cond-no", outcome="No")
    # Missing price → excluded
    store_flow_scan(result=_scan("cond-np", p_market=None))
    track_market_if_new(condition_id="cond-np")
    mark_market_resolved(condition_id="cond-np", outcome="Yes")

    entries = get_paper_entries()
    assert len(entries) == 1
    assert entries[0]["win"] == 1
    assert abs(entries[0]["price"] - 0.3) < 1e-9


def test_dashboard_payload_includes_paper(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_PERSISTENCE", "1")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    store_flow_scan(result=_scan("cond-a"))
    track_market_if_new(condition_id="cond-a")
    mark_market_resolved(condition_id="cond-a", outcome="Yes")

    d = client.get("/dashboard/data").json()
    assert d["paper"]["overall"]["trades"] == 1
    assert len(d["paper"]["curve"]) == 1
    assert d["paper"]["stake"] == 100.0


def test_paper_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_PERSISTENCE", "1")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("PAPER_STAKE", "50")
    monkeypatch.setenv("PAPER_FEE", "0")
    store_flow_scan(result=_scan("cond-a", p_market=0.5))
    track_market_if_new(condition_id="cond-a")
    mark_market_resolved(condition_id="cond-a", outcome="Yes")

    d = client.get("/dashboard/data").json()
    assert d["paper"]["stake"] == 50.0
    assert d["paper"]["overall"]["pnl"] == 50.0
