"""Tests for server-side deployed capital tracking (BL-05)."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ENABLE_PERSISTENCE", "1")

from crypto_news_parser import storage
from crypto_news_parser.main import app

client = TestClient(app)


def _reset(tmp_path):
    """Point DB at a fresh temp file and reset state."""
    db = str(tmp_path / "test.sqlite3")
    with patch.dict(os.environ, {"DB_PATH": db, "ENABLE_PERSISTENCE": "1"}):
        yield db


@pytest.fixture()
def fresh_db(tmp_path):
    db = str(tmp_path / "cap.sqlite3")
    with patch.dict(os.environ, {"DB_PATH": db, "ENABLE_PERSISTENCE": "1"}):
        yield db


# ---------------------------------------------------------------------------
# Storage-layer unit tests
# ---------------------------------------------------------------------------


def test_get_deployed_capital_starts_at_zero(fresh_db) -> None:
    assert storage.get_deployed_capital() == 0.0


def test_reserve_capital_increases_balance(fresh_db) -> None:
    new_total = storage.reserve_capital(50.0)
    assert new_total == pytest.approx(50.0)
    assert storage.get_deployed_capital() == pytest.approx(50.0)


def test_reserve_capital_accumulates(fresh_db) -> None:
    storage.reserve_capital(30.0)
    storage.reserve_capital(20.0)
    assert storage.get_deployed_capital() == pytest.approx(50.0)


def test_reserve_capital_idempotent_with_bet_id(fresh_db) -> None:
    storage.reserve_capital(50.0, bet_id="bet-abc")
    storage.reserve_capital(50.0, bet_id="bet-abc")  # duplicate — should not double-count
    assert storage.get_deployed_capital() == pytest.approx(50.0)


def test_release_capital(fresh_db) -> None:
    storage.reserve_capital(100.0)
    new_total = storage.release_capital(40.0)
    assert new_total == pytest.approx(60.0)


def test_release_capital_floors_at_zero(fresh_db) -> None:
    storage.reserve_capital(10.0)
    new_total = storage.release_capital(999.0)
    assert new_total == 0.0


def test_reset_deployed_capital(fresh_db) -> None:
    storage.reserve_capital(200.0)
    storage.reset_deployed_capital()
    assert storage.get_deployed_capital() == 0.0


# ---------------------------------------------------------------------------
# API-level tests: /risk with auto_reserve
# ---------------------------------------------------------------------------


def test_risk_auto_reserve_go_updates_ledger(fresh_db) -> None:
    resp = client.post("/risk", json={
        "p_model": 0.70,
        "p_market": 0.50,
        "bankroll": 1000.0,
        "auto_reserve": True,
        "bet_id": "test-001",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "GO"
    bet = data["bet_size"]
    assert bet > 0
    # Ledger should now reflect the reserved amount
    deployed = storage.get_deployed_capital()
    assert deployed == pytest.approx(bet, abs=0.01)


def test_risk_auto_reserve_no_go_does_not_update_ledger(fresh_db) -> None:
    resp = client.post("/risk", json={
        "p_model": 0.51,  # edge too thin
        "p_market": 0.50,
        "bankroll": 1000.0,
        "auto_reserve": True,
    })
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "NO_GO"
    assert storage.get_deployed_capital() == 0.0


def test_risk_auto_reserve_uses_server_deployed_not_request_field(fresh_db) -> None:
    """auto_reserve ignores the caller's `deployed` and uses the server ledger."""
    # Pre-load 280 into ledger (cap is 300 = 30% of 1000)
    storage.reserve_capital(280.0)
    resp = client.post("/risk", json={
        "p_model": 0.70,
        "p_market": 0.50,
        "bankroll": 1000.0,
        "deployed": 0.0,  # caller claims 0, but server sees 280
        "auto_reserve": True,
    })
    assert resp.status_code == 200
    # With 280 already deployed, adding any bet would exceed 300 cap → NO_GO
    assert resp.json()["verdict"] == "NO_GO"


def test_risk_no_auto_reserve_uses_request_deployed(fresh_db) -> None:
    """Without auto_reserve, caller-supplied deployed is used (legacy behaviour)."""
    resp = client.post("/risk", json={
        "p_model": 0.70,
        "p_market": 0.50,
        "bankroll": 1000.0,
        "deployed": 0.0,
        "auto_reserve": False,
    })
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "GO"
    # Ledger unchanged
    assert storage.get_deployed_capital() == 0.0


# ---------------------------------------------------------------------------
# /deployed endpoints
# ---------------------------------------------------------------------------


def test_get_deployed_endpoint(fresh_db) -> None:
    storage.reserve_capital(75.0)
    resp = client.get("/deployed")
    assert resp.status_code == 200
    assert resp.json()["deployed"] == pytest.approx(75.0)


def test_release_deployed_endpoint(fresh_db) -> None:
    storage.reserve_capital(100.0)
    resp = client.post("/deployed/release", json={"amount": 40.0})
    assert resp.status_code == 200
    assert resp.json()["deployed"] == pytest.approx(60.0)


def test_reset_deployed_endpoint(fresh_db) -> None:
    storage.reserve_capital(500.0)
    resp = client.post("/deployed/reset")
    assert resp.status_code == 200
    assert resp.json()["deployed"] == 0.0
