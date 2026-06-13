"""In-process scheduler: periodic flow scans + resolution polls + Telegram alerts.

Opt-in via SCHEDULER_ENABLE=1. Replaces the n8n workflow for wallet-flow:
the FastAPI app starts these loops in its lifespan, so running the server
is the whole deployment.

Environment:
    SCHEDULER_ENABLE        "1" to start the loops (default: disabled)
    SCAN_INTERVAL_HOURS     hours between flow scans (default 4)
    POLL_INTERVAL_MINUTES   minutes between resolution polls (default 15)
    SCAN_TOP_N              markets per scan (default 20)
    SCAN_MAX_WALLETS        wallet-metadata cap per market (default 300)
    ALERT_MIN_TIER          minimum tier that triggers an alert (default HIGH)
    TELEGRAM_BOT_TOKEN      bot token; alerts are a no-op when unset
    TELEGRAM_CHAT_ID        chat id; alerts are a no-op when unset
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

from .models import FlowScanRequest, FlowScanResponse, PollResolutionsResponse
from .storage import persistence_enabled

logger = logging.getLogger(__name__)

_TIER_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

# Let the server settle before the first (multi-minute) scan.
SCAN_STARTUP_DELAY_SECONDS = 30.0

ScanFn = Callable[[FlowScanRequest], Awaitable[FlowScanResponse]]
PollFn = Callable[[], Awaitable[PollResolutionsResponse]]


def scheduler_enabled() -> bool:
    return os.getenv("SCHEDULER_ENABLE", "").strip().lower() in {"1", "true", "yes"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        logger.warning("scheduler: invalid %s, using default %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, default))


# ---------- Telegram ----------

def _send_telegram_sync(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as exc:
        logger.warning("scheduler: telegram send failed: %s", exc)
        return False


async def send_telegram(text: str) -> bool:
    return await asyncio.to_thread(_send_telegram_sync, text)


def format_alert(r: dict[str, Any]) -> str:
    """Telegram message for one flow-scan market result (FlowMarketResult dump)."""
    p = r.get("p_market_at_scan")
    implied = f"{p * 100:.0f}%" if p is not None else "?"
    return (
        f"⚠️ {r['risk_tier']} informed-flow signal\n"
        f"{r['market_question']}\n"
        f"side: {r['dominant_side']} | score: {r['signal_score']}\n"
        f"new wallets: {r['dominant_side_count']} (${r['dominant_side_usdc']:,.0f})\n"
        f"market YES price at scan: {implied}\n"
        f"expires: ⏳ {r['days_to_resolution']} days"
    )


# ---------- Single cycles ----------

async def scan_once(scan_fn: ScanFn) -> int:
    """Run one flow scan and alert on markets at/above ALERT_MIN_TIER. Returns alert count."""
    req = FlowScanRequest(
        top_n=_env_int("SCAN_TOP_N", 20),
        max_wallets=_env_int("SCAN_MAX_WALLETS", 300),
    )
    resp = await scan_fn(req)
    min_tier = os.getenv("ALERT_MIN_TIER", "HIGH").strip().upper()
    min_rank = _TIER_RANK.get(min_tier, _TIER_RANK["HIGH"])
    alerts = 0
    for r in resp.results:
        if _TIER_RANK.get(r.risk_tier, 0) >= min_rank:
            await send_telegram(format_alert(r.model_dump()))
            alerts += 1
    logger.info("scheduler: scan done — %d markets, %d alerts", resp.scanned, alerts)
    return alerts


async def poll_once(poll_fn: PollFn) -> int:
    """Run one resolution poll (skipped without persistence). Returns resolved count."""
    if not persistence_enabled():
        return 0
    resp = await poll_fn()
    if resp.resolved:
        logger.info("scheduler: %d markets resolved", len(resp.resolved))
    return len(resp.resolved)


# ---------- Loops ----------

async def _loop(
    name: str,
    interval_seconds: float,
    cycle: Callable[[], Awaitable[Any]],
    initial_delay_seconds: float = 0.0,
) -> None:
    if initial_delay_seconds > 0:
        await asyncio.sleep(initial_delay_seconds)
    while True:
        try:
            await cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler: %s cycle failed; continuing", name)
        await asyncio.sleep(interval_seconds)


def start(scan_fn: ScanFn, poll_fn: PollFn) -> list[asyncio.Task]:
    """Spawn the scheduler loops. Must be called from a running event loop."""
    # Uvicorn doesn't give app loggers a handler; without this, scheduler INFO
    # lines (scan done, alerts sent) are invisible. No-op if already configured.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    scan_interval = _env_float("SCAN_INTERVAL_HOURS", 4.0) * 3600
    poll_interval = _env_float("POLL_INTERVAL_MINUTES", 15.0) * 60
    tasks = [
        asyncio.create_task(
            _loop("flow-scan", scan_interval, lambda: scan_once(scan_fn),
                  initial_delay_seconds=SCAN_STARTUP_DELAY_SECONDS),
            name="scheduler-flow-scan",
        ),
        asyncio.create_task(
            _loop("poll-resolutions", poll_interval, lambda: poll_once(poll_fn)),
            name="scheduler-poll-resolutions",
        ),
    ]
    logger.info(
        "scheduler: started (scan every %.1fh, poll every %.0fmin)",
        scan_interval / 3600, poll_interval / 60,
    )
    return tasks
