"""Tests for the market-category capability: classify, filter, tag, breakdown, reset."""
from __future__ import annotations

import sqlite3
from datetime import UTC

from fastapi.testclient import TestClient

import crypto_news_parser.wallet_flow as wf
from crypto_news_parser.main import app
from crypto_news_parser.storage import (
    archive_and_reset_flow_data,
    get_category_breakdown,
    mark_market_resolved,
    store_flow_scan,
    track_market_if_new,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_crypto():
    assert wf.classify_category("Will Bitcoin reach $80k in July?") == "crypto"
    assert wf.classify_category("Will MegaETH perform an airdrop?") == "crypto"


def test_classify_sports():
    assert wf.classify_category("Los Angeles Dodgers vs. Chicago White Sox") == "sports"
    assert wf.classify_category("Qatar vs. Switzerland: O/U 2.5") == "sports"


def test_classify_macro_and_politics():
    assert wf.classify_category("Will the Fed cut interest rates by 25 bps?") == "macro"
    assert wf.classify_category("Will Trump win the 2028 election?") == "politics"


def test_classify_other():
    assert wf.classify_category("Will it rain in Narnia tomorrow?") == "other"


def test_classify_uses_tags():
    assert wf.classify_category("Ambiguous title", tags=["Crypto", "Bitcoin"]) == "crypto"


# ---------------------------------------------------------------------------
# Scanner category filtering (mocked fetch)
# ---------------------------------------------------------------------------


def _market(cid, question, days=10, cat=None):
    m = {
        "conditionId": cid,
        "question": question,
        "endDate": None,
        "liquidity": 50_000,
        "volumeNum": 100_000,
    }
    # emulate the tag-fetch path stamping _category
    if cat:
        m["_category"] = cat
    from datetime import datetime, timedelta
    m["endDate"] = (datetime.now(UTC) + timedelta(days=days)).isoformat()
    return m


def test_fetch_top_markets_filters_to_allowlist(monkeypatch):
    crypto = [_market("c1", "Will Bitcoin hit $80k?", cat="crypto")]

    def fake_pool(category):
        return crypto if category == "crypto" else []

    monkeypatch.setattr(wf, "_fetch_category_pool", fake_pool)
    out = wf.fetch_top_markets(top_n=20, categories=["crypto"])
    assert len(out) == 1
    assert out[0]["_category"] == "crypto"


def test_analyze_market_tags_category(monkeypatch):
    monkeypatch.setattr(wf, "fetch_trades_for_market", lambda cid: [])
    m = _market("c1", "Will Ethereum dip to $1500?", cat="crypto")
    m["_days_to_resolution"] = 10
    m["_liquidity_usdc"] = 50_000
    m["_volume_usdc"] = 100_000
    m["_volume_1wk_usdc"] = 0
    m["_volume_1mo_usdc"] = 0
    r = wf.analyze_market(m, {})
    assert r["category"] == "crypto"


# ---------------------------------------------------------------------------
# Storage: category column + breakdown
# ---------------------------------------------------------------------------


def _scan(cid, cat, dom="YES", price=0.5, tier="HIGH"):
    return {
        "market_id": cid,
        "market_question": f"Q {cid}",
        "category": cat,
        "signal_score": 75,
        "risk_tier": tier,
        "dominant_side": dom,
        "dominant_side_usdc": 10_000.0,
        "p_market_at_scan": price,
    }


def test_store_flow_scan_persists_category(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    store_flow_scan(result=_scan("c1", "crypto"))
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    cat = conn.execute("SELECT category FROM flow_scans WHERE condition_id='c1'").fetchone()[0]
    assert cat == "crypto"


def test_category_breakdown(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    # crypto: win at 0.5 -> +$98 (2% fee); sports: loss -> -$100
    store_flow_scan(result=_scan("c1", "crypto", dom="YES", price=0.5))
    track_market_if_new(condition_id="c1")
    mark_market_resolved(condition_id="c1", outcome="Yes")
    store_flow_scan(result=_scan("s1", "sports", dom="YES", price=0.5))
    track_market_if_new(condition_id="s1")
    mark_market_resolved(condition_id="s1", outcome="No")

    bd = {r["category"]: r for r in get_category_breakdown()}
    assert bd["crypto"]["n"] == 1 and bd["crypto"]["pnl"] == 98.0
    assert bd["sports"]["n"] == 1 and bd["sports"]["pnl"] == -100.0
    assert bd["crypto"]["lift"] == 0.5  # win 100% - implied 50%


def test_dashboard_data_includes_by_category(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_PERSISTENCE", "1")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    store_flow_scan(result=_scan("c1", "crypto"))
    track_market_if_new(condition_id="c1")
    mark_market_resolved(condition_id="c1", outcome="Yes")

    d = client.get("/dashboard/data").json()
    assert any(c["category"] == "crypto" for c in d["by_category"])


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_archive_and_reset_clears_and_archives(monkeypatch, tmp_path):
    db = tmp_path / "data.sqlite3"
    monkeypatch.setenv("DB_PATH", str(db))
    store_flow_scan(result=_scan("c1", "crypto"))
    track_market_if_new(condition_id="c1")

    archive = archive_and_reset_flow_data()

    assert Path_exists(archive)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM flow_scans").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tracked_markets").fetchone()[0] == 0
    # archive still has the data
    aconn = sqlite3.connect(archive)
    assert aconn.execute("SELECT COUNT(*) FROM flow_scans").fetchone()[0] == 1


def Path_exists(p: str) -> bool:
    from pathlib import Path
    return Path(p).exists()
