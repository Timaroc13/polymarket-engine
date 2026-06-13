"""Tests for the KPI dashboard (dashboard capability)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from crypto_news_parser.main import app
from crypto_news_parser.storage import (
    get_calibration_timeline,
    get_recent_scans,
    mark_market_resolved,
    store_flow_scan,
    track_market_if_new,
)

client = TestClient(app)


def _scan(condition_id, tier="HIGH", dominant="YES", p_market=0.5, score=75):
    return {
        "market_id": condition_id,
        "market_question": f"Q {condition_id}",
        "signal_score": score,
        "risk_tier": tier,
        "dominant_side": dominant,
        "dominant_side_usdc": 10_000.0,
        "p_market_at_scan": p_market,
    }


def _resolve(condition_id, outcome):
    track_market_if_new(condition_id=condition_id)
    mark_market_resolved(condition_id=condition_id, outcome=outcome)


# ---------------------------------------------------------------------------
# Timeline math
# ---------------------------------------------------------------------------


def test_timeline_cumulative_lift(monkeypatch, tmp_path):
    """Spec scenario: win, loss, win at implied 0.5 → lifts +0.5, 0.0, ~+0.1667."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))

    for i, outcome in enumerate(["Yes", "No", "Yes"]):
        cid = f"cond-{i}"
        store_flow_scan(result=_scan(cid, dominant="YES", p_market=0.5))
        _resolve(cid, outcome)

    points = get_calibration_timeline()
    assert [p["n"] for p in points] == [1, 2, 3]
    assert points[0]["lift"] == 0.5
    assert points[1]["lift"] == 0.0
    assert abs(points[2]["lift"] - 0.1667) < 1e-3
    # All three were HIGH tier, so the HIGH series matches
    assert points[2]["n_high"] == 3
    assert points[2]["lift_high"] == points[2]["lift"]


def test_timeline_skips_excluded_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    store_flow_scan(result=_scan("cond-null", dominant=None))
    _resolve("cond-null", "Yes")
    store_flow_scan(result=_scan("cond-ok"))
    _resolve("cond-ok", "Yes")

    points = get_calibration_timeline()
    assert len(points) == 1
    assert points[0]["n"] == 1


def test_recent_scans_newest_first_with_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))
    for i in range(5):
        store_flow_scan(result=_scan(f"cond-{i}", score=i))
    scans = get_recent_scans(limit=3)
    assert len(scans) == 3
    assert [s["condition_id"] for s in scans] == ["cond-4", "cond-3", "cond-2"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_dashboard_page_serves_html():
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "wallet-flow" in resp.text


def test_dashboard_data_requires_persistence(monkeypatch):
    monkeypatch.delenv("ENABLE_PERSISTENCE", raising=False)
    resp = client.get("/dashboard/data")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PERSISTENCE_DISABLED"


def test_dashboard_data_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_PERSISTENCE", "1")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.sqlite3"))

    store_flow_scan(result=_scan("cond-a", dominant="YES", p_market=0.6))
    _resolve("cond-a", "Yes")
    store_flow_scan(result=_scan("cond-open", tier="MEDIUM", score=50))
    track_market_if_new(condition_id="cond-open")

    resp = client.get("/dashboard/data")
    assert resp.status_code == 200
    d = resp.json()
    assert d["calibration"]["overall"]["n"] == 1
    assert len(d["timeline"]) == 1
    assert abs(d["timeline"][0]["lift"] - 0.4) < 1e-6
    assert len(d["recent_scans"]) == 2
    assert d["stats"]["scans_total"] == 2
    assert d["stats"]["tracked_resolved"] == 1
    assert d["stats"]["tracked_unresolved"] == 1
    assert d["gates"]["gate1_n"] == 30


def test_dashboard_endpoints_skip_api_key(monkeypatch):
    import crypto_news_parser.main as main_mod
    monkeypatch.setattr(main_mod, "REQUIRED_API_KEY", "sekret")
    assert client.get("/dashboard").status_code == 200
    # data endpoint: no 401 even with API key configured (400 only re: persistence)
    monkeypatch.delenv("ENABLE_PERSISTENCE", raising=False)
    assert client.get("/dashboard/data").status_code == 400
