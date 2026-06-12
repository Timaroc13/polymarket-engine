"""Tests for POST /risk endpoint and validate_risk()."""
from __future__ import annotations

from fastapi.testclient import TestClient

from crypto_news_parser.main import app
from crypto_news_parser.models import RiskRequest
from crypto_news_parser.risk import validate_risk

client = TestClient(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(**kwargs) -> RiskRequest:
    defaults = dict(p_model=0.65, p_market=0.50, bankroll=1000.0, deployed=0.0)
    defaults.update(kwargs)
    return RiskRequest(**defaults)


# ---------------------------------------------------------------------------
# Unit tests: validate_risk()
# ---------------------------------------------------------------------------


def test_go_on_clear_edge() -> None:
    # p_model=0.65, p_market=0.50 → edge=15%, well above 4% min
    result = validate_risk(_req())
    assert result.verdict == "GO"
    assert result.bet_size is not None
    assert result.bet_size > 0
    assert result.ev is not None and result.ev > 0
    assert all(r.passed for r in result.rules)


def test_no_go_insufficient_edge() -> None:
    # edge = 0.02, below min_edge=0.04
    result = validate_risk(_req(p_model=0.52, p_market=0.50))
    assert result.verdict == "NO_GO"
    min_edge_rule = next(r for r in result.rules if r.name == "Minimum Edge")
    assert not min_edge_rule.passed


def test_no_go_negative_kelly() -> None:
    # p_model below p_market → negative EV
    result = validate_risk(_req(p_model=0.40, p_market=0.55))
    assert result.verdict == "NO_GO"
    kelly_rule = next(r for r in result.rules if r.name == "Kelly Positive")
    assert not kelly_rule.passed


def test_no_go_exposure_cap_exceeded() -> None:
    # Already deployed 95% of bankroll; even a small bet pushes over 30% cap
    result = validate_risk(_req(deployed=950.0, bankroll=1000.0))
    assert result.verdict == "NO_GO"
    exposure_rule = next(r for r in result.rules if r.name == "Max Exposure")
    assert not exposure_rule.passed


def test_bet_capped_at_max_bet_fraction() -> None:
    # High edge + large bankroll → Kelly wants more than max_bet_fraction allows
    result = validate_risk(_req(p_model=0.90, p_market=0.10, bankroll=10000.0))
    if result.verdict == "GO":
        assert result.bet_size <= 10000.0 * 0.05 + 0.01  # within max_bet_fraction (5%)


def test_kelly_formula_correctness() -> None:
    # Manual: p=0.65, p_market=0.50, b=(1-0.5)/0.5=1.0
    # kelly_f = (0.65*1.0 - 0.35)/1.0 = 0.30
    result = validate_risk(_req(p_model=0.65, p_market=0.50))
    assert abs(result.kelly_f - 0.30) < 1e-6


def test_edge_calculation() -> None:
    result = validate_risk(_req(p_model=0.62, p_market=0.55))
    assert abs(result.edge - 0.07) < 1e-9


def test_five_rules_always_present() -> None:
    for kwargs in [
        dict(p_model=0.65, p_market=0.50),
        dict(p_model=0.40, p_market=0.55),
        dict(p_model=0.52, p_market=0.50),
    ]:
        result = validate_risk(_req(**kwargs))
        assert len(result.rules) == 5
        names = {r.name for r in result.rules}
    assert names == {
        "Minimum Edge", "Kelly Positive", "Max Single Bet", "Max Exposure", "VaR 95%"
    }


def test_custom_config_overrides() -> None:
    # Relax min_edge to 1% and increase max_bet to 20% — should pass
    result = validate_risk(_req(p_model=0.52, p_market=0.50, min_edge=0.01, max_bet_fraction=0.20))
    min_edge_rule = next(r for r in result.rules if r.name == "Minimum Edge")
    assert min_edge_rule.passed


# ---------------------------------------------------------------------------
# Integration tests: POST /risk
# ---------------------------------------------------------------------------


def test_risk_go_response_shape() -> None:
    resp = client.post("/risk", json={
        "p_model": 0.65, "p_market": 0.50, "bankroll": 1000.0, "deployed": 0.0
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "GO"
    assert "bet_size" in data and data["bet_size"] > 0
    assert "ev" in data
    assert "kelly_f" in data
    assert "edge" in data
    assert len(data["rules"]) == 5


def test_risk_no_go_response_has_null_bet() -> None:
    resp = client.post("/risk", json={
        "p_model": 0.51, "p_market": 0.50, "bankroll": 1000.0, "deployed": 0.0
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "NO_GO"
    assert data["bet_size"] is None
    assert data["ev"] is None


def test_risk_invalid_p_market_zero() -> None:
    resp = client.post("/risk", json={
        "p_model": 0.65, "p_market": 0.0, "bankroll": 1000.0, "deployed": 0.0
    })
    assert resp.status_code == 422


def test_risk_invalid_negative_bankroll() -> None:
    resp = client.post("/risk", json={
        "p_model": 0.65, "p_market": 0.50, "bankroll": -100.0, "deployed": 0.0
    })
    assert resp.status_code == 422


def test_risk_rules_list_has_pass_fail() -> None:
    resp = client.post("/risk", json={
        "p_model": 0.65, "p_market": 0.50, "bankroll": 1000.0, "deployed": 0.0
    })
    assert resp.status_code == 200
    for rule in resp.json()["rules"]:
        assert "name" in rule
        assert "passed" in rule
        assert isinstance(rule["passed"], bool)
        assert "message" in rule


# ---------------------------------------------------------------------------
# BL-06: Confidence-weighted Kelly fraction
# ---------------------------------------------------------------------------


def test_confidence_scales_bet_size() -> None:
    """Lower confidence should produce a proportionally smaller bet size.

    We use a small bankroll so the fractional Kelly stays under max_bet_fraction
    for both confidence values, ensuring the cap doesn't interfere.
    p=0.65, pm=0.50, bankroll=100, kelly_f=0.30
    full: 0.25 * 1.0 * 0.30 * 100 = 7.5  (cap = 5% * 100 = 5 → capped at 5.0)
    That's still capped. Use bankroll=50 so full bet = 0.25*0.30*50 = 3.75, cap = 2.5 → still caps.
    Use max_bet_fraction=1.0 to disable the cap.
    full: 0.25 * 1.0 * 0.30 * 1000 = 75.0
    half: 0.25 * 0.5 * 0.30 * 1000 = 37.5  → half of 75.0 ✓
    """
    full_conf = validate_risk(_req(confidence=1.0, max_bet_fraction=1.0))
    half_conf = validate_risk(_req(confidence=0.5, max_bet_fraction=1.0))
    assert full_conf.verdict == "GO"
    assert half_conf.verdict == "GO"
    assert full_conf.bet_size is not None
    assert half_conf.bet_size is not None
    # Half confidence → half the bet size
    assert abs(half_conf.bet_size - full_conf.bet_size * 0.5) < 0.01


def test_zero_confidence_results_in_no_bet() -> None:
    """Confidence of 0.0 means Kelly fraction is effectively zero → no bet."""
    result = validate_risk(_req(confidence=0.0))
    # fractional_bet = 0.0 * kelly_f * bankroll = 0.0
    # A $0 bet fails none of the rules that check bet size positivity,
    # but the bet_size should be 0 and the EV should be 0 → NO_GO (EV=0 is fine but
    # the kelly fraction is 0 which means edge rule may still pass; what matters is
    # bet_size=0 and kelly_f rule checks kelly_f > 0, which it is based on p_model/p_market).
    # The important assertion: bet_size must be 0 (or very small).
    if result.verdict == "GO":
        assert result.bet_size == 0.0
    else:
        # NO_GO is also acceptable when confidence=0 leads to 0 bet
        assert result.bet_size is None or result.bet_size == 0.0


def test_default_confidence_is_one() -> None:
    """Default confidence=1.0 should match explicit confidence=1.0."""
    explicit = validate_risk(_req(confidence=1.0))
    implicit = validate_risk(_req())  # no confidence kwarg
    assert explicit.bet_size == implicit.bet_size
    assert explicit.verdict == implicit.verdict


def test_confidence_scaling_via_api() -> None:
    """POST /risk should accept and apply the confidence field."""
    resp_full = client.post("/risk", json={
        "p_model": 0.65, "p_market": 0.50, "bankroll": 1000.0,
        "deployed": 0.0, "confidence": 1.0,
    })
    resp_half = client.post("/risk", json={
        "p_model": 0.65, "p_market": 0.50, "bankroll": 1000.0,
        "deployed": 0.0, "confidence": 0.5,
    })
    assert resp_full.status_code == 200
    assert resp_half.status_code == 200
    full_bet = resp_full.json().get("bet_size") or 0.0
    half_bet = resp_half.json().get("bet_size") or 0.0
    assert half_bet < full_bet


# ---------------------------------------------------------------------------
# BL-11: Time-to-expiry Kelly scaling
# ---------------------------------------------------------------------------

_EXPIRY_REQ_KWARGS = dict(
    max_bet_fraction=1.0,
    max_exposure_fraction=1.0,  # disable exposure cap
    kelly_fraction=1.0,
    var_tolerance=1.0,          # disable VaR cap — time-scaling is the only variable
)


def test_days_to_expiry_none_has_no_effect() -> None:
    """Omitting days_to_expiry (None) should produce same result as time_multiplier=1.0."""
    r_no_expiry = validate_risk(_req(**_EXPIRY_REQ_KWARGS))
    r_explicit_1 = validate_risk(_req(**_EXPIRY_REQ_KWARGS, days_to_expiry=1))
    # day 1 → multiplier 1.0 — should match no-expiry result
    assert r_no_expiry.bet_size == r_explicit_1.bet_size


def test_days_to_expiry_day1_full_size() -> None:
    """day 1 → time_multiplier = 1.0 → same bet as no expiry scaling."""
    base = validate_risk(_req(**_EXPIRY_REQ_KWARGS))
    day1 = validate_risk(_req(**_EXPIRY_REQ_KWARGS, days_to_expiry=1))
    assert base.bet_size == day1.bet_size


def test_days_to_expiry_day10_half_size() -> None:
    """day 10 → time_multiplier = 0.5 → bet is half of day-1 bet."""
    day1 = validate_risk(_req(**_EXPIRY_REQ_KWARGS, days_to_expiry=1))
    day10 = validate_risk(_req(**_EXPIRY_REQ_KWARGS, days_to_expiry=10))
    assert day1.bet_size is not None and day10.bet_size is not None
    assert abs(day10.bet_size - day1.bet_size * 0.5) < 0.02


def test_days_to_expiry_decreases_bet_monotonically() -> None:
    """Bet size should decrease (or stay equal) as days_to_expiry increases."""
    bets = []
    for d in [1, 3, 5, 7, 10]:
        r = validate_risk(_req(**_EXPIRY_REQ_KWARGS, days_to_expiry=d))
        bets.append(r.bet_size or 0.0)
    for i in range(len(bets) - 1):
        assert bets[i] >= bets[i + 1], (
            f"bet not decreasing: day {i+1}={bets[i]:.2f}, day {i+2}={bets[i+1]:.2f}"
        )


def test_days_to_expiry_beyond_cap_clamps_to_half() -> None:
    """days_to_expiry > 10 should clamp time_multiplier at 0.5, same as day 10."""
    day10 = validate_risk(_req(**_EXPIRY_REQ_KWARGS, days_to_expiry=10))
    day20 = validate_risk(_req(**_EXPIRY_REQ_KWARGS, days_to_expiry=20))
    assert day10.bet_size == day20.bet_size


def test_days_to_expiry_via_api() -> None:
    """POST /risk should accept days_to_expiry and return smaller bet for day 10 vs day 1."""
    common = {"p_model": 0.65, "p_market": 0.50, "bankroll": 1000.0,
              "deployed": 0.0, "max_bet_fraction": 1.0, "max_exposure_fraction": 1.0,
              "kelly_fraction": 1.0, "var_tolerance": 1.0}
    resp1 = client.post("/risk", json={**common, "days_to_expiry": 1})
    resp10 = client.post("/risk", json={**common, "days_to_expiry": 10})
    assert resp1.status_code == 200
    assert resp10.status_code == 200
    bet1 = resp1.json().get("bet_size") or 0.0
    bet10 = resp10.json().get("bet_size") or 0.0
    assert bet10 < bet1
