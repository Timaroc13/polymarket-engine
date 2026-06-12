"""Tests for the wallet-flow capability: scoring, storage, calibration, and API."""
from __future__ import annotations

import time
from unittest.mock import patch

from fastapi.testclient import TestClient

import crypto_news_parser.main as main_mod
from crypto_news_parser.main import app
from crypto_news_parser.storage import (
    get_flow_calibration,
    mark_market_resolved,
    store_flow_scan,
    track_market_if_new,
)
from crypto_news_parser.wallet_flow import (
    build_positions_from_trades,
    detect,
    extract_yes_price,
)

client = TestClient(app)

NOW = 1_750_000_000  # fixed scan time for deterministic ages


def _new_wallet_position(wallet: str, side: str, usdc: float, age_days: int = 2) -> dict:
    """A position held by a 'new' wallet (1 market, first trade age_days ago)."""
    return {
        "wallet_address": wallet,
        "outcome_index": 0 if side == "YES" else 1,
        "side": side,
        "usdc_size": usdc,
        "first_trade_ts_in_market": NOW - age_days * 86400,
        "position_created_at_ts": NOW - age_days * 86400,
        "wallet_first_tx_ever_ts": NOW - age_days * 86400,
        "wallet_total_markets_traded": 1,
    }


def _veteran_position(wallet: str, side: str, usdc: float) -> dict:
    pos = _new_wallet_position(wallet, side, usdc, age_days=200)
    pos["wallet_total_markets_traded"] = 7
    return pos


def _detect(positions, volume_1wk=0.0, volume_1mo=0.0, **kwargs):
    return detect(
        "cond-test", "Will X happen?", 3, 50_000.0, positions,
        total_volume=100_000.0, volume_1wk=volume_1wk, volume_1mo=volume_1mo,
        now_ts=NOW, **kwargs,
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_directional_new_wallet_flow_scores_high():
    # 12 new wallets, $25k on YES; 1 new wallet $500 on NO; 65% burst.
    positions = [_new_wallet_position(f"0xyes{i}", "YES", 25_000 / 12) for i in range(12)]
    positions.append(_new_wallet_position("0xno1", "NO", 500))

    r = _detect(positions, volume_1wk=65_000, volume_1mo=100_000)

    assert r["dominant_side"] == "YES"
    # count(12): +20, capital($25k): +30, burst(0.65): +20, dominance(12/13): +10
    assert r["signal_score"] == 80
    assert r["risk_tier"] == "HIGH"
    assert r["new_wallet_count_yes"] == 12
    assert r["new_wallet_count_no"] == 1


def test_no_new_wallet_activity_is_low_with_null_dominant_side():
    positions = [_veteran_position("0xvet1", "YES", 50_000)]
    r = _detect(positions)
    assert r["signal_score"] == 0
    assert r["risk_tier"] == "LOW"
    assert r["dominant_side"] is None
    assert r["new_wallet_count_total"] == 0


def test_hedged_bilateral_flow_gets_no_dominance_points():
    # 5 new wallets each side, equal capital below the $5k tier.
    positions = [_new_wallet_position(f"0xy{i}", "YES", 400) for i in range(5)]
    positions += [_new_wallet_position(f"0xn{i}", "NO", 400) for i in range(5)]

    r = _detect(positions)
    # dom_count=5 → +10; capital $2k → 0; burst 0 → 0; count_pct 0.5 → 0
    assert r["signal_score"] == 10
    assert r["dominant_side_count_pct"] == 0.5


def test_burst_tiers():
    positions = [_new_wallet_position("0xa", "YES", 100)]
    low = _detect(positions, volume_1wk=10, volume_1mo=100)["signal_score"]
    mid = _detect(positions, volume_1wk=35, volume_1mo=100)["signal_score"]
    high = _detect(positions, volume_1wk=70, volume_1mo=100)["signal_score"]
    # single wallet: only dominance points (+10) as baseline
    assert mid == low + 10
    assert high == low + 20


def test_tier_cutoffs():
    # 3 wallets, $6k dominant, 100% dominance, no burst → 10+15+10+5+... compute:
    # count(3): +10, capital(6k): +15, dominance(1.0): +10 → 35 = LOW
    positions = [_new_wallet_position(f"0x{i}", "YES", 2_000) for i in range(3)]
    r = _detect(positions)
    assert r["signal_score"] == 35
    assert r["risk_tier"] == "LOW"

    # Add burst 0.35 (+10) → 45 = MEDIUM
    r = _detect(positions, volume_1wk=35, volume_1mo=100)
    assert r["signal_score"] == 45
    assert r["risk_tier"] == "MEDIUM"

    # 10 wallets, $25k, burst 0.65 → 20+30+20+10 = 80 = HIGH
    positions = [_new_wallet_position(f"0x{i}", "YES", 2_500) for i in range(10)]
    r = _detect(positions, volume_1wk=65, volume_1mo=100)
    assert r["signal_score"] == 80
    assert r["risk_tier"] == "HIGH"


def test_old_or_multimarket_wallets_are_not_new():
    too_old = _new_wallet_position("0xold", "YES", 10_000, age_days=20)
    multi = _new_wallet_position("0xmulti", "YES", 10_000)
    multi["wallet_total_markets_traded"] = 2
    r = _detect([too_old, multi])
    assert r["new_wallet_count_total"] == 0


# ---------------------------------------------------------------------------
# Position reconstruction
# ---------------------------------------------------------------------------


def _trade(wallet, side, size, price, outcome_idx=0, ts=NOW):
    return {
        "proxyWallet": wallet,
        "side": side,
        "size": size,
        "price": price,
        "outcomeIndex": outcome_idx,
        "timestamp": ts,
    }


def test_build_positions_nets_buys_and_sells():
    trades = [
        _trade("0xa", "BUY", 100, 0.5),   # +50
        _trade("0xa", "SELL", 60, 0.5),   # -30
    ]
    positions = build_positions_from_trades(trades)
    assert len(positions) == 1
    assert positions[0]["wallet_address"] == "0xa"
    assert positions[0]["side"] == "YES"
    assert abs(positions[0]["usdc_size"] - 20.0) < 1e-9


def test_build_positions_drops_closed_and_net_short():
    trades = [
        _trade("0xa", "BUY", 100, 0.5),
        _trade("0xa", "SELL", 100, 0.5),  # fully closed
        _trade("0xb", "SELL", 100, 0.5),  # net short
    ]
    assert build_positions_from_trades(trades) == []


def test_build_positions_skips_invalid_rows_and_maps_no_side():
    trades = [
        {"proxyWallet": None, "side": "BUY", "size": 10, "price": 0.5, "outcomeIndex": 0},
        _trade("0xa", "BUY", 0, 0.5),          # zero size
        _trade("0xb", "BUY", 100, 0.4, outcome_idx=1),  # NO side
    ]
    positions = build_positions_from_trades(trades)
    assert len(positions) == 1
    assert positions[0]["side"] == "NO"


def test_analyze_market_max_wallets_caps_metadata_lookups(monkeypatch):
    import crypto_news_parser.wallet_flow as wf

    # 5 wallets with increasing position sizes; only top 2 should get metadata lookups.
    trades = [_trade(f"0xw{i}", "BUY", 100 * (i + 1), 0.5) for i in range(5)]
    monkeypatch.setattr(wf, "fetch_trades_for_market", lambda cid: trades)

    looked_up: list[str] = []

    def fake_meta(wallet, cache):
        looked_up.append(wallet)
        # Age against real time: analyze_market scores with now_ts = time.time().
        meta = {"first_trade_ts": int(time.time()) - 86400, "markets_traded": 1}
        cache[wallet] = meta
        return meta

    monkeypatch.setattr(wf, "fetch_wallet_metadata", fake_meta)

    market = {"conditionId": "cond-cap", "question": "Q?", "_days_to_resolution": 3,
              "_liquidity_usdc": 50_000.0, "_volume_usdc": 100_000.0,
              "_volume_1wk_usdc": 0.0, "_volume_1mo_usdc": 0.0}
    r = wf.analyze_market(market, {}, max_wallets=2)

    assert sorted(looked_up) == ["0xw3", "0xw4"]  # the two largest positions
    assert r["new_wallet_count_total"] == 2  # uncapped wallets treated as not-new


def test_extract_yes_price_handles_string_and_list():
    assert extract_yes_price({"outcomePrices": '["0.62", "0.38"]'}) == 0.62
    assert extract_yes_price({"outcomePrices": [0.4, 0.6]}) == 0.4
    assert extract_yes_price({"outcomePrices": "not json"}) is None
    assert extract_yes_price({}) is None


# ---------------------------------------------------------------------------
# Storage + calibration
# ---------------------------------------------------------------------------


def _scan_result(condition_id, tier="HIGH", dominant="YES", p_market=0.6, score=75):
    return {
        "market_id": condition_id,
        "market_question": f"Q for {condition_id}",
        "signal_score": score,
        "risk_tier": tier,
        "dominant_side": dominant,
        "dominant_side_usdc": 10_000.0,
        "p_market_at_scan": p_market,
    }


def test_store_flow_scan_appends_history(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    id1 = store_flow_scan(result=_scan_result("cond-h"))
    id2 = store_flow_scan(result=_scan_result("cond-h", p_market=0.7))
    assert id2 > id1


def test_track_market_if_new_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    track_market_if_new(condition_id="cond-x", question="Q?")
    track_market_if_new(condition_id="cond-x", question="Q?")  # no raise


def test_calibration_win_rate_and_lift(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))

    # Market A: dominant YES @ implied 0.6, resolved Yes → win
    store_flow_scan(result=_scan_result("cond-a", dominant="YES", p_market=0.6))
    track_market_if_new(condition_id="cond-a")
    mark_market_resolved(condition_id="cond-a", outcome="Yes")

    # Market B: dominant NO @ yes-price 0.7 (implied 0.3), resolved Yes → loss
    store_flow_scan(result=_scan_result("cond-b", dominant="NO", p_market=0.7))
    track_market_if_new(condition_id="cond-b")
    mark_market_resolved(condition_id="cond-b", outcome="Yes")

    report = get_flow_calibration()
    overall = report["overall"]
    assert overall["n"] == 2
    assert overall["wins"] == 1
    assert overall["win_rate"] == 0.5
    assert abs(overall["avg_implied"] - 0.45) < 1e-6
    assert abs(overall["lift"] - 0.05) < 1e-6
    assert report["tiers"]["HIGH"]["n"] == 2
    assert report["tiers"]["LOW"]["n"] == 0
    assert report["excluded"] == 0


def test_calibration_uses_latest_scan_per_market(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))

    # Earlier scan says NO; the latest scan before resolution says YES.
    store_flow_scan(result=_scan_result("cond-l", dominant="NO", p_market=0.5))
    store_flow_scan(result=_scan_result("cond-l", dominant="YES", p_market=0.5))
    track_market_if_new(condition_id="cond-l")
    mark_market_resolved(condition_id="cond-l", outcome="Yes")

    report = get_flow_calibration()
    assert report["overall"]["n"] == 1
    assert report["overall"]["wins"] == 1


def test_calibration_excludes_null_dominant_and_unparseable_outcomes(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))

    store_flow_scan(result=_scan_result("cond-null", dominant=None))
    track_market_if_new(condition_id="cond-null")
    mark_market_resolved(condition_id="cond-null", outcome="Yes")

    store_flow_scan(result=_scan_result("cond-weird", dominant="YES"))
    track_market_if_new(condition_id="cond-weird")
    mark_market_resolved(condition_id="cond-weird", outcome="50-50 split")

    report = get_flow_calibration()
    assert report["overall"]["n"] == 0
    assert report["excluded"] == 2
    assert report["overall"]["win_rate"] is None
    assert report["overall"]["lift"] is None


def test_calibration_unresolved_markets_not_counted(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    store_flow_scan(result=_scan_result("cond-open"))
    track_market_if_new(condition_id="cond-open")

    report = get_flow_calibration()
    assert report["overall"]["n"] == 0


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _full_scan_result(condition_id="cond-api", **overrides):
    base = {
        "market_id": condition_id,
        "market_question": "Will it?",
        "signal_score": 80,
        "risk_tier": "HIGH",
        "dominant_side": "YES",
        "dominant_side_usdc": 25_000.0,
        "dominant_side_count": 12,
        "dominant_side_count_pct": 0.92,
        "new_wallet_count_yes": 12,
        "new_wallet_count_no": 1,
        "new_wallet_usdc_yes": 25_000.0,
        "new_wallet_usdc_no": 500.0,
        "new_wallet_count_total": 13,
        "new_wallet_total_usdc": 25_500.0,
        "recent_burst_pct": 0.65,
        "p_market_at_scan": 0.61,
        "days_to_resolution": 3,
        "new_wallets": [],
        "summary": "test",
    }
    base.update(overrides)
    return base


def test_flow_scan_default(monkeypatch):
    monkeypatch.delenv("ENABLE_PERSISTENCE", raising=False)
    with patch.object(main_mod, "run_scan", return_value=[_full_scan_result()]) as mock_scan:
        resp = client.post("/flow-scan", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["scanned"] == 1
    assert data["stored"] is False
    assert data["results"][0]["risk_tier"] == "HIGH"
    assert data["results"][0]["p_market_at_scan"] == 0.61
    _, kwargs = mock_scan.call_args
    assert kwargs["top_n"] == 20
    assert kwargs["condition_id"] is None


def test_flow_scan_single_condition_id(monkeypatch):
    monkeypatch.delenv("ENABLE_PERSISTENCE", raising=False)
    with patch.object(main_mod, "run_scan", return_value=[_full_scan_result("0xabc")]) as mock_scan:
        resp = client.post("/flow-scan", json={"condition_id": "0xabc"})
    assert resp.status_code == 200
    assert resp.json()["scanned"] == 1
    _, kwargs = mock_scan.call_args
    assert kwargs["condition_id"] == "0xabc"


def test_flow_scan_persists_and_tracks(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_PERSISTENCE", "1")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))

    with patch.object(main_mod, "run_scan", return_value=[_full_scan_result("cond-p")]):
        resp = client.post("/flow-scan", json={})
    assert resp.status_code == 200
    assert resp.json()["stored"] is True

    # Resolve it and confirm calibration sees it
    mark_market_resolved(condition_id="cond-p", outcome="Yes")
    resp = client.get("/flow-calibration")
    assert resp.status_code == 200
    assert resp.json()["overall"]["n"] == 1
    assert resp.json()["overall"]["wins"] == 1


def test_flow_scan_top_n_capped_at_50(monkeypatch):
    resp = client.post("/flow-scan", json={"top_n": 100})
    assert resp.status_code == 422


def test_flow_scan_requires_api_key(monkeypatch):
    monkeypatch.setattr(main_mod, "REQUIRED_API_KEY", "sekret")
    resp = client.post("/flow-scan", json={})
    assert resp.status_code == 401
    resp = client.get("/flow-calibration")
    assert resp.status_code == 401


def test_flow_calibration_requires_persistence(monkeypatch):
    monkeypatch.delenv("ENABLE_PERSISTENCE", raising=False)
    resp = client.get("/flow-calibration")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PERSISTENCE_DISABLED"
