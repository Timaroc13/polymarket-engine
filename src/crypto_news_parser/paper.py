"""Flat-stake paper-trading replay over resolved wallet-flow signals.

Pure simulation over the rows the engine already logs: a virtual stake on
the dominant side of every qualifying resolved signal at its scan-time
implied price, with a fee haircut on winnings. Adds the *money* lens that
calibration lift (probability lens) cannot provide.

Deliberately flat-stake: the flow signal yields a side + tier, not a
probability, so Kelly sizing here would be fake precision (see BL-14).
"""
from __future__ import annotations

import os
from typing import Any

from .storage import get_paper_entries

DEFAULT_STAKE = 100.0
DEFAULT_FEE = 0.02  # haircut on gross winnings (fees + slippage proxy)

_TIERS = ("LOW", "MEDIUM", "HIGH")


def _bucket() -> dict[str, float]:
    return {"trades": 0, "wins": 0, "staked": 0.0, "pnl": 0.0}


def _finalize(b: dict[str, float]) -> dict[str, Any]:
    trades = int(b["trades"])
    return {
        "trades": trades,
        "wins": int(b["wins"]),
        "win_rate": round(b["wins"] / trades, 4) if trades else None,
        "staked": round(b["staked"], 2),
        "pnl": round(b["pnl"], 2),
        "roi": round(b["pnl"] / b["staked"], 4) if b["staked"] else None,
    }


def simulate_paper_trading(
    entries: list[dict[str, Any]],
    stake: float = DEFAULT_STAKE,
    fee: float = DEFAULT_FEE,
) -> dict[str, Any]:
    """Replay entries ({win, price, tier, resolved_at}) in order.

    Win pays stake * (1/price - 1) * (1 - fee); loss costs the stake.
    """
    overall = _bucket()
    tiers = {t: _bucket() for t in _TIERS}
    curve: list[dict[str, Any]] = []
    equity = 0.0
    equity_high = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for e in entries:
        price = float(e["price"])
        if not (0.0 < price < 1.0):
            continue
        win = bool(e["win"])
        pnl = stake * (1.0 / price - 1.0) * (1.0 - fee) if win else -stake

        tier = e["tier"] if e["tier"] in tiers else "LOW"
        for b in (overall, tiers[tier]):
            b["trades"] += 1
            b["wins"] += 1 if win else 0
            b["staked"] += stake
            b["pnl"] += pnl

        equity += pnl
        if tier == "HIGH":
            equity_high += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        curve.append({
            "resolved_at": e.get("resolved_at"),
            "n": int(overall["trades"]),
            "equity": round(equity, 2),
            "equity_high": round(equity_high, 2),
        })

    return {
        "stake": stake,
        "fee": fee,
        "overall": _finalize(overall),
        "tiers": {t: _finalize(b) for t, b in tiers.items()},
        "max_drawdown": round(max_drawdown, 2),
        "curve": curve,
    }


def get_paper_report() -> dict[str, Any]:
    """Replay the stored resolutions with env-configured stake/fee."""
    try:
        stake = float(os.getenv("PAPER_STAKE", "") or DEFAULT_STAKE)
        fee = float(os.getenv("PAPER_FEE", "") or DEFAULT_FEE)
    except ValueError:
        stake, fee = DEFAULT_STAKE, DEFAULT_FEE
    return simulate_paper_trading(get_paper_entries(), stake=stake, fee=fee)
