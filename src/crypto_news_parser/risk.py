"""Prediction market risk validation — the 5 hard rules.

This module implements the core math from the predict-market-risk rundown.
All functions are pure and deterministic given their inputs.

Formulas used:
    b (decimal odds)  = (1 - p_market) / p_market
    Kelly f*          = (p_model * b - (1 - p_model)) / b
                      = (p_model - p_market) / (1 - p_market)
    Fractional Kelly  = kelly_fraction * f* * bankroll
    EV                = p_model * profit - (1 - p_model) * bet
    VaR 95%           = -(mu - 1.645 * sigma)   [positive = loss]
                        where mu    = p_model * profit - (1-p_model) * bet
                              sigma = (profit + bet) * sqrt(p_model * (1-p_model))
"""
from __future__ import annotations

import math

from .models import RiskRequest, RiskResponse, RuleResult


def validate_risk(req: RiskRequest) -> RiskResponse:
    p = req.p_model
    q = 1.0 - p
    pm = req.p_market

    # Decimal odds: $1 bet wins $(1/p_market - 1) profit
    b = (1.0 - pm) / pm

    # Raw Kelly fraction f*
    kelly_f = (p * b - q) / b  # equivalent to (p_model - p_market) / (1 - p_market)

    edge = p - pm

    # Time-to-expiry multiplier: day 1 = 1.0×, day 10 = 0.5× (linear), clamped to [0.5, 1.0]
    if req.days_to_expiry is not None:
        time_multiplier = max(0.5, 1.0 - (req.days_to_expiry - 1) * 0.5 / 9)
    else:
        time_multiplier = 1.0

    # Fractional Kelly bet size — scaled by parser confidence and time-to-expiry
    effective_kelly_fraction = req.kelly_fraction * req.confidence * time_multiplier
    fractional_bet = effective_kelly_fraction * kelly_f * req.bankroll
    fractional_bet = max(0.0, fractional_bet)  # can't be negative

    # Hard caps
    max_bet = req.max_bet_fraction * req.bankroll
    bet_size = min(fractional_bet, max_bet)

    # Profit and EV at proposed bet_size
    profit = bet_size * b
    ev = p * profit - q * bet_size

    # VaR 95%: worst-case loss at 95th percentile for this single bet
    mu = p * profit - q * bet_size
    sigma = (profit + bet_size) * math.sqrt(p * q)
    var_95_normal = -(mu - 1.645 * sigma)
    var_95 = min(var_95_normal, bet_size)  # can't lose more than the stake on a binary market

    # --- 5 rules ---

    rule_min_edge = RuleResult(
        name="Minimum Edge",
        passed=edge >= req.min_edge,
        value=round(edge, 4),
        threshold=req.min_edge,
        message=(
            f"Edge {edge:.1%} ≥ {req.min_edge:.1%}"
            if edge >= req.min_edge
            else f"Edge {edge:.1%} below minimum {req.min_edge:.1%}"
        ),
    )

    rule_kelly_positive = RuleResult(
        name="Kelly Positive",
        passed=kelly_f > 0,
        value=round(kelly_f, 4),
        threshold=0.0,
        message=(
            f"Kelly f* = {kelly_f:.4f} (positive EV)"
            if kelly_f > 0
            else f"Kelly f* = {kelly_f:.4f} (negative EV — do not bet)"
        ),
    )

    rule_max_bet = RuleResult(
        name="Max Single Bet",
        passed=bet_size <= max_bet,
        value=round(bet_size, 2),
        threshold=round(max_bet, 2),
        message=(
            f"Bet ${bet_size:.2f} ≤ cap ${max_bet:.2f}"
            if bet_size <= max_bet
            else f"Bet ${bet_size:.2f} exceeds cap ${max_bet:.2f}"
        ),
    )

    new_exposure = req.deployed + bet_size
    max_exposure = req.max_exposure_fraction * req.bankroll
    rule_max_exposure = RuleResult(
        name="Max Exposure",
        passed=new_exposure <= max_exposure,
        value=round(new_exposure, 2),
        threshold=round(max_exposure, 2),
        message=(
            f"Total exposure ${new_exposure:.2f} ≤ cap ${max_exposure:.2f}"
            if new_exposure <= max_exposure
            else f"Total exposure ${new_exposure:.2f} exceeds cap ${max_exposure:.2f}"
        ),
    )

    var_cap = req.var_tolerance * req.bankroll
    rule_var = RuleResult(
        name="VaR 95%",
        passed=var_95 <= var_cap,
        value=round(var_95, 2),
        threshold=round(var_cap, 2),
        message=(
            f"VaR95 ${var_95:.2f} ≤ tolerance ${var_cap:.2f}"
            if var_95 <= var_cap
            else f"VaR95 ${var_95:.2f} exceeds tolerance ${var_cap:.2f}"
        ),
    )

    rules = [rule_min_edge, rule_kelly_positive, rule_max_bet, rule_max_exposure, rule_var]
    all_passed = all(r.passed for r in rules)

    return RiskResponse(
        verdict="GO" if all_passed else "NO_GO",
        bet_size=round(bet_size, 2) if all_passed else None,
        ev=round(ev, 2) if all_passed else None,
        kelly_full=round(kelly_f * req.bankroll, 2),
        rules=rules,
        edge=round(edge, 4),
        kelly_f=round(kelly_f, 4),
    )
