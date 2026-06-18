"""Tests for /track-market and /poll-resolutions endpoints (BL-01)."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import crypto_news_parser.main as main_mod
from crypto_news_parser.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enable_persistence(monkeypatch):
    monkeypatch.setenv("ENABLE_PERSISTENCE", "1")


def _clob_resolved(winner: str):
    """Real CLOB market shape: resolution lives on the tokens array."""
    return {
        "closed": True,
        "tokens": [
            {"outcome": "Yes", "price": 1 if winner == "Yes" else 0, "winner": winner == "Yes"},
            {"outcome": "No", "price": 1 if winner == "No" else 0, "winner": winner == "No"},
        ],
    }


def _clob_open():
    return {"closed": False, "tokens": [
        {"outcome": "Yes", "price": 0.5, "winner": False},
        {"outcome": "No", "price": 0.5, "winner": False},
    ]}


# ---------------------------------------------------------------------------
# /poll-resolutions — persistence disabled
# ---------------------------------------------------------------------------


def test_poll_resolutions_requires_persistence(monkeypatch):
    monkeypatch.delenv("ENABLE_PERSISTENCE", raising=False)
    resp = client.post("/poll-resolutions")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PERSISTENCE_DISABLED"


# ---------------------------------------------------------------------------
# /track-market — persistence disabled
# ---------------------------------------------------------------------------


def test_track_market_requires_persistence(monkeypatch):
    monkeypatch.delenv("ENABLE_PERSISTENCE", raising=False)
    resp = client.post("/track-market", json={"condition_id": "abc123"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PERSISTENCE_DISABLED"


# ---------------------------------------------------------------------------
# /poll-resolutions — no unresolved markets
# ---------------------------------------------------------------------------


def test_poll_resolutions_empty_db(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.sqlite3"))

    resp = client.post("/poll-resolutions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["checked"] == 0
    assert data["resolved"] == []
    assert data["errors"] == []


# ---------------------------------------------------------------------------
# /poll-resolutions — market not resolved yet
# ---------------------------------------------------------------------------


def test_poll_resolutions_skips_unresolved_market(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.sqlite3"))

    # Track a market first
    resp = client.post("/track-market", json={
        "condition_id": "cond-not-resolved",
        "question": "Will BTC hit $200k?",
    })
    assert resp.status_code == 200

    # Polymarket returns unresolved (closed=False)
    with patch.object(main_mod, "_fetch_polymarket_market", return_value=_clob_open()):
        resp = client.post("/poll-resolutions")

    assert resp.status_code == 200
    data = resp.json()
    assert data["checked"] == 1
    assert data["resolved"] == []


# ---------------------------------------------------------------------------
# /poll-resolutions — market resolved → feedback stored
# ---------------------------------------------------------------------------


def test_poll_resolutions_resolved_market(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.sqlite3"))

    # Track a market
    resp = client.post("/track-market", json={
        "condition_id": "cond-resolved-yes",
        "question": "Will ETH reach $5k?",
        "input_id": "my-signal-001",
    })
    assert resp.status_code == 200

    # Polymarket returns resolved (winner token)
    with patch.object(main_mod, "_fetch_polymarket_market", return_value=_clob_resolved("Yes")):
        resp = client.post("/poll-resolutions")

    assert resp.status_code == 200
    data = resp.json()
    assert data["checked"] == 1
    assert len(data["resolved"]) == 1
    item = data["resolved"][0]
    assert item["condition_id"] == "cond-resolved-yes"
    assert item["outcome"] == "Yes"
    assert isinstance(item["feedback_id"], int)


# ---------------------------------------------------------------------------
# /poll-resolutions — already resolved markets are skipped on second poll
# ---------------------------------------------------------------------------


def test_poll_resolutions_already_resolved_not_polled_again(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.sqlite3"))

    # Track and resolve a market
    client.post("/track-market", json={"condition_id": "cond-idempotent", "input_id": "x1"})

    poly_data = _clob_resolved("No")
    with patch.object(main_mod, "_fetch_polymarket_market", return_value=poly_data):
        resp1 = client.post("/poll-resolutions")
    assert len(resp1.json()["resolved"]) == 1

    # Second poll — market is now marked resolved in DB; should not be fetched again
    fetch_calls = []
    def mock_fetch(condition_id):
        fetch_calls.append(condition_id)
        return poly_data

    with patch.object(main_mod, "_fetch_polymarket_market", side_effect=mock_fetch):
        resp2 = client.post("/poll-resolutions")

    assert resp2.json()["checked"] == 0
    assert fetch_calls == []  # nothing was fetched


# ---------------------------------------------------------------------------
# /poll-resolutions — Polymarket fetch failure adds to errors list
# ---------------------------------------------------------------------------


def test_extract_resolution_maps_winner_index_to_yes_no():
    # token[0] winner → YES; token[1] winner → NO (matches dominant_side convention)
    assert main_mod._extract_resolution(_clob_resolved("Yes")) == "Yes"
    assert main_mod._extract_resolution(_clob_resolved("No")) == "No"


def test_extract_resolution_open_market_returns_none():
    assert main_mod._extract_resolution(_clob_open()) is None
    assert main_mod._extract_resolution({"closed": True}) is None  # closed, no tokens yet


def test_extract_resolution_price_fallback_when_no_winner_flag():
    data = {"closed": True, "tokens": [
        {"outcome": "Yes", "price": 0.0},
        {"outcome": "No", "price": 1.0},
    ]}
    assert main_mod._extract_resolution(data) == "No"


def test_extract_resolution_non_binary_returns_raw_label():
    data = {"closed": True, "tokens": [
        {"outcome": "Team A", "price": 0, "winner": False},
        {"outcome": "Team B", "price": 1, "winner": True},
        {"outcome": "Draw", "price": 0, "winner": False},
    ]}
    # 3-outcome market → raw label (calibration excludes non-YES/NO)
    assert main_mod._extract_resolution(data) == "Team B"


def test_poll_resolutions_fetch_error_adds_to_errors(monkeypatch, tmp_path):
    _enable_persistence(monkeypatch)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.sqlite3"))

    client.post("/track-market", json={"condition_id": "cond-fetch-fail", "input_id": "x2"})

    with patch.object(main_mod, "_fetch_polymarket_market", return_value=None):
        resp = client.post("/poll-resolutions")

    data = resp.json()
    assert data["checked"] == 1
    assert data["resolved"] == []
    assert len(data["errors"]) == 1
    assert "cond-fetch-fail" in data["errors"][0]
