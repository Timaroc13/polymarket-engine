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

    # Polymarket returns unresolved
    poly_data = {"resolved": False, "outcome": None}
    with patch.object(main_mod, "_fetch_polymarket_market", return_value=poly_data):
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

    # Polymarket returns resolved
    poly_data = {"resolved": True, "outcome": "Yes"}
    with patch.object(main_mod, "_fetch_polymarket_market", return_value=poly_data):
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

    poly_data = {"resolved": True, "outcome": "No"}
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
