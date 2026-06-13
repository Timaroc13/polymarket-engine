from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def persistence_enabled() -> bool:
    return os.getenv("ENABLE_PERSISTENCE") == "1"


def db_path() -> str:
    # Default to a local file. In Cloud Run, /tmp is writable.
    return os.getenv("DB_PATH", str(Path(__file__).resolve().parents[2] / "data.sqlite3"))


def _connect() -> sqlite3.Connection:
    path = db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_CAPITAL_LEDGER_ID = 1  # single-row ledger


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS parse_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            input_id TEXT,
            source_url TEXT,
            source_name TEXT,
            source_published_at TEXT,
            text TEXT NOT NULL,
            response_json TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            model_version TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            parse_run_id INTEGER,
            input_id TEXT,
            text TEXT,
            expected_json TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY(parse_run_id) REFERENCES parse_runs(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS capital_ledger (
            id INTEGER PRIMARY KEY,
            deployed REAL NOT NULL DEFAULT 0.0,
            updated_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS capital_reservations (
            bet_id TEXT PRIMARY KEY,
            amount REAL NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    # Ensure the single ledger row exists
    conn.execute(
        "INSERT OR IGNORE INTO capital_ledger (id, deployed, updated_at) VALUES (?, 0.0, 0)",
        (_CAPITAL_LEDGER_ID,),
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS flow_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            condition_id TEXT NOT NULL,
            question TEXT,
            signal_score INTEGER NOT NULL,
            risk_tier TEXT NOT NULL,
            dominant_side TEXT,
            dominant_side_usdc REAL NOT NULL DEFAULT 0.0,
            p_market_at_scan REAL,
            result_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_flow_scans_condition"
        " ON flow_scans(condition_id, created_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tracked_markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            condition_id TEXT NOT NULL UNIQUE,
            question TEXT,
            parse_id INTEGER,
            input_id TEXT,
            resolved INTEGER NOT NULL DEFAULT 0,
            outcome TEXT,
            resolved_at INTEGER,
            FOREIGN KEY(parse_id) REFERENCES parse_runs(id) ON DELETE SET NULL
        )
        """
    )
    conn.commit()


@dataclass(frozen=True)
class StoredParse:
    parse_id: int


def store_parse_run(
    *,
    input_id: str | None,
    source_url: str | None,
    source_name: str | None,
    source_published_at: str | None,
    text: str,
    response: dict[str, Any],
) -> StoredParse:
    conn = _connect()
    try:
        init_db(conn)
        cur = conn.execute(
            """
            INSERT INTO parse_runs (
                created_at, input_id, source_url, source_name, source_published_at,
                text, response_json, schema_version, model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                input_id,
                source_url,
                source_name,
                source_published_at,
                text,
                json.dumps(response, ensure_ascii=False),
                str(response.get("schema_version") or ""),
                str(response.get("model_version") or ""),
            ),
        )
        conn.commit()
        return StoredParse(parse_id=int(cur.lastrowid))
    finally:
        conn.close()


def _load_parse_text(conn: sqlite3.Connection, parse_id: int) -> str | None:
    row = conn.execute("SELECT text FROM parse_runs WHERE id = ?", (parse_id,)).fetchone()
    if row is None:
        return None
    return str(row["text"])


def store_feedback(
    *,
    parse_id: int | None,
    input_id: str | None,
    text: str | None,
    expected: dict[str, Any],
    notes: str | None,
) -> int:
    conn = _connect()
    try:
        init_db(conn)
        stored_text: str | None = text
        parse_run_id: int | None = None
        if parse_id is not None:
            parse_text = _load_parse_text(conn, parse_id)
            if parse_text is None:
                raise ValueError("parse_id not found")
            parse_run_id = parse_id
            stored_text = parse_text

        cur = conn.execute(
            """
            INSERT INTO feedback (created_at, parse_run_id, input_id, text, expected_json, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                parse_run_id,
                input_id,
                stored_text,
                json.dumps(expected, ensure_ascii=False),
                notes,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


@dataclass(frozen=True)
class TrackedMarket:
    id: int
    condition_id: str
    question: str | None
    parse_id: int | None
    input_id: str | None


def track_market(
    *,
    condition_id: str,
    question: str | None = None,
    parse_id: int | None = None,
    input_id: str | None = None,
) -> int:
    """Insert a new tracked market; returns its id. Raises if condition_id already tracked."""
    conn = _connect()
    try:
        init_db(conn)
        cur = conn.execute(
            """
            INSERT INTO tracked_markets (created_at, condition_id, question, parse_id, input_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(time.time()), condition_id, question, parse_id, input_id),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_unresolved_markets() -> list[TrackedMarket]:
    """Return all tracked markets that have not yet been resolved."""
    conn = _connect()
    try:
        init_db(conn)
        rows = conn.execute(
            "SELECT id, condition_id, question, parse_id, input_id"
            " FROM tracked_markets WHERE resolved = 0"
        ).fetchall()
        return [
            TrackedMarket(
                id=int(r["id"]),
                condition_id=str(r["condition_id"]),
                question=r["question"],
                parse_id=r["parse_id"],
                input_id=r["input_id"],
            )
            for r in rows
        ]
    finally:
        conn.close()


def mark_market_resolved(*, condition_id: str, outcome: str) -> None:
    """Mark a tracked market as resolved with the given outcome."""
    conn = _connect()
    try:
        init_db(conn)
        conn.execute(
            """
            UPDATE tracked_markets
            SET resolved = 1, outcome = ?, resolved_at = ?
            WHERE condition_id = ?
            """,
            (outcome, int(time.time()), condition_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_deployed_capital() -> float:
    """Return the current server-side deployed capital."""
    conn = _connect()
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT deployed FROM capital_ledger WHERE id = ?", (_CAPITAL_LEDGER_ID,)
        ).fetchone()
        return float(row["deployed"]) if row else 0.0
    finally:
        conn.close()


def reserve_capital(amount: float, bet_id: str | None = None) -> float:
    """Atomically add *amount* to deployed capital.

    If *bet_id* is provided, the reservation is idempotent: a second call
    with the same bet_id returns the already-recorded deployed total without
    double-counting.

    Returns the new deployed total after reservation.
    """
    conn = _connect()
    try:
        init_db(conn)
        with conn:  # transaction
            if bet_id is not None:
                existing = conn.execute(
                    "SELECT amount FROM capital_reservations WHERE bet_id = ?", (bet_id,)
                ).fetchone()
                if existing is not None:
                    # Already reserved — return current total without adding again
                    row = conn.execute(
                        "SELECT deployed FROM capital_ledger WHERE id = ?", (_CAPITAL_LEDGER_ID,)
                    ).fetchone()
                    return float(row["deployed"])
                conn.execute(
                    "INSERT INTO capital_reservations (bet_id, amount, created_at)"
                    " VALUES (?, ?, ?)",
                    (bet_id, amount, int(time.time())),
                )
            conn.execute(
                "UPDATE capital_ledger SET deployed = deployed + ?, updated_at = ? WHERE id = ?",
                (amount, int(time.time()), _CAPITAL_LEDGER_ID),
            )
            row = conn.execute(
                "SELECT deployed FROM capital_ledger WHERE id = ?", (_CAPITAL_LEDGER_ID,)
            ).fetchone()
            return float(row["deployed"])
    finally:
        conn.close()


def release_capital(amount: float) -> float:
    """Subtract *amount* from deployed capital (floored at 0). Returns new total."""
    conn = _connect()
    try:
        init_db(conn)
        with conn:
            conn.execute(
                "UPDATE capital_ledger SET deployed = MAX(0.0, deployed - ?),"
                " updated_at = ? WHERE id = ?",
                (amount, int(time.time()), _CAPITAL_LEDGER_ID),
            )
            row = conn.execute(
                "SELECT deployed FROM capital_ledger WHERE id = ?", (_CAPITAL_LEDGER_ID,)
            ).fetchone()
            return float(row["deployed"])
    finally:
        conn.close()


def reset_deployed_capital() -> None:
    """Reset deployed capital to zero (e.g. start of a new trading session)."""
    conn = _connect()
    try:
        init_db(conn)
        with conn:
            conn.execute(
                "UPDATE capital_ledger SET deployed = 0.0, updated_at = ? WHERE id = ?",
                (int(time.time()), _CAPITAL_LEDGER_ID),
            )
    finally:
        conn.close()


def store_flow_scan(*, result: dict[str, Any]) -> int:
    """Store one flow-scan result row. Returns the row id."""
    conn = _connect()
    try:
        init_db(conn)
        cur = conn.execute(
            """
            INSERT INTO flow_scans (
                created_at, condition_id, question, signal_score, risk_tier,
                dominant_side, dominant_side_usdc, p_market_at_scan, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                str(result.get("market_id") or ""),
                result.get("market_question"),
                int(result.get("signal_score") or 0),
                str(result.get("risk_tier") or "LOW"),
                result.get("dominant_side"),
                float(result.get("dominant_side_usdc") or 0.0),
                result.get("p_market_at_scan"),
                json.dumps(result, ensure_ascii=False),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def track_market_if_new(*, condition_id: str, question: str | None = None) -> None:
    """Register a market for resolution tracking, ignoring duplicates."""
    conn = _connect()
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO tracked_markets (created_at, condition_id, question)
            VALUES (?, ?, ?)
            """,
            (int(time.time()), condition_id, question),
        )
        conn.commit()
    finally:
        conn.close()


def _calibration_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Latest scan per resolved market (at or before resolution), with outcome.

    Shared by get_flow_calibration and get_calibration_timeline.
    """
    return conn.execute(
        """
        SELECT fs.risk_tier, fs.dominant_side, fs.p_market_at_scan, tm.outcome,
               tm.resolved_at
        FROM tracked_markets tm
        JOIN flow_scans fs ON fs.id = (
            SELECT fs2.id FROM flow_scans fs2
            WHERE fs2.condition_id = tm.condition_id
              AND (tm.resolved_at IS NULL OR fs2.created_at <= tm.resolved_at)
            ORDER BY fs2.created_at DESC, fs2.id DESC
            LIMIT 1
        )
        WHERE tm.resolved = 1
        ORDER BY tm.resolved_at ASC
        """
    ).fetchall()


def _qualify_row(row: sqlite3.Row) -> tuple[int, float | None, str] | None:
    """Map a calibration row to (win, implied, tier), or None when excluded."""
    dom = row["dominant_side"]
    outcome = (str(row["outcome"]).strip().upper() if row["outcome"] is not None else "")
    if dom not in ("YES", "NO") or outcome not in ("YES", "NO"):
        return None
    win = 1 if outcome == dom else 0
    yes_price = row["p_market_at_scan"]
    implied: float | None = None
    if yes_price is not None:
        implied = float(yes_price) if dom == "YES" else 1.0 - float(yes_price)
    return win, implied, str(row["risk_tier"])


def get_flow_calibration() -> dict[str, Any]:
    """Join the latest flow scan per market with resolved outcomes.

    For each resolved tracked market, uses the most recent scan at or before
    resolution time (falls back to the latest scan when resolved_at is null).
    Rows qualify when the scan has a non-null dominant side and the outcome
    maps to YES/NO; everything else is counted in `excluded`.

    Returns {"overall": bucket, "tiers": {tier: bucket}, "excluded": int}
    where bucket = {n, wins, win_rate, avg_implied, lift}.
    """
    conn = _connect()
    try:
        init_db(conn)
        rows = _calibration_rows(conn)

        def _empty_bucket() -> dict[str, Any]:
            return {"n": 0, "wins": 0, "_implied_sum": 0.0, "_implied_n": 0}

        overall = _empty_bucket()
        tiers: dict[str, dict[str, Any]] = {
            "LOW": _empty_bucket(),
            "MEDIUM": _empty_bucket(),
            "HIGH": _empty_bucket(),
        }
        excluded = 0

        for r in rows:
            qualified = _qualify_row(r)
            if qualified is None:
                excluded += 1
                continue
            win, implied, tier = qualified
            tier = tier if tier in tiers else "LOW"
            for bucket in (overall, tiers[tier]):
                bucket["n"] += 1
                bucket["wins"] += win
                if implied is not None:
                    bucket["_implied_sum"] += implied
                    bucket["_implied_n"] += 1

        def _finalize(bucket: dict[str, Any]) -> dict[str, Any]:
            n = bucket["n"]
            win_rate = (bucket["wins"] / n) if n > 0 else None
            avg_implied = (
                bucket["_implied_sum"] / bucket["_implied_n"]
                if bucket["_implied_n"] > 0
                else None
            )
            lift = (
                round(win_rate - avg_implied, 4)
                if win_rate is not None and avg_implied is not None
                else None
            )
            return {
                "n": n,
                "wins": bucket["wins"],
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
                "avg_implied": round(avg_implied, 4) if avg_implied is not None else None,
                "lift": lift,
            }

        return {
            "overall": _finalize(overall),
            "tiers": {k: _finalize(v) for k, v in tiers.items()},
            "excluded": excluded,
        }
    finally:
        conn.close()


def get_calibration_timeline() -> list[dict[str, Any]]:
    """Lift evolution: one point per qualifying resolution, in resolution order.

    Each point carries cumulative calibration for overall and for HIGH tier:
    {resolved_at, n, win_rate, avg_implied, lift, n_high, lift_high}.
    """
    conn = _connect()
    try:
        init_db(conn)
        rows = _calibration_rows(conn)
    finally:
        conn.close()

    def _cum(state: dict[str, float], win: int, implied: float | None) -> None:
        state["n"] += 1
        state["wins"] += win
        if implied is not None:
            state["imp_sum"] += implied
            state["imp_n"] += 1

    def _lift(state: dict[str, float]) -> tuple[float | None, float | None]:
        if state["n"] == 0:
            return None, None
        win_rate = state["wins"] / state["n"]
        if state["imp_n"] == 0:
            return round(win_rate, 4), None
        return round(win_rate, 4), round(win_rate - state["imp_sum"] / state["imp_n"], 4)

    overall = {"n": 0, "wins": 0, "imp_sum": 0.0, "imp_n": 0}
    high = {"n": 0, "wins": 0, "imp_sum": 0.0, "imp_n": 0}
    points: list[dict[str, Any]] = []
    for r in rows:
        qualified = _qualify_row(r)
        if qualified is None:
            continue
        win, implied, tier = qualified
        _cum(overall, win, implied)
        if tier == "HIGH":
            _cum(high, win, implied)
        win_rate, lift = _lift(overall)
        win_rate_high, lift_high = _lift(high)
        avg_implied = (
            round(overall["imp_sum"] / overall["imp_n"], 4) if overall["imp_n"] else None
        )
        points.append({
            "resolved_at": r["resolved_at"],
            "n": int(overall["n"]),
            "win_rate": win_rate,
            "avg_implied": avg_implied,
            "lift": lift,
            "n_high": int(high["n"]),
            "lift_high": lift_high,
        })
    return points


def get_paper_entries() -> list[dict[str, Any]]:
    """Qualifying resolved signals for the paper-trading replay, resolution order.

    Each entry: {win, price (dominant-side implied at scan), tier, resolved_at}.
    Rows without a usable price are excluded (consistent with calibration).
    """
    conn = _connect()
    try:
        init_db(conn)
        rows = _calibration_rows(conn)
    finally:
        conn.close()

    entries: list[dict[str, Any]] = []
    for r in rows:
        qualified = _qualify_row(r)
        if qualified is None:
            continue
        win, implied, tier = qualified
        if implied is None or not (0.0 < implied < 1.0):
            continue
        entries.append({
            "win": win,
            "price": implied,
            "tier": tier,
            "resolved_at": r["resolved_at"],
        })
    return entries


def get_recent_scans(limit: int = 50) -> list[dict[str, Any]]:
    """Most recent flow-scan rows, newest first."""
    conn = _connect()
    try:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT created_at, condition_id, question, signal_score, risk_tier,
                   dominant_side, dominant_side_usdc, p_market_at_scan
            FROM flow_scans ORDER BY id DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_dashboard_stats() -> dict[str, Any]:
    """Operational counters for the dashboard."""
    conn = _connect()
    try:
        init_db(conn)
        scans_total = int(conn.execute("SELECT COUNT(*) FROM flow_scans").fetchone()[0])
        last_scan = conn.execute("SELECT MAX(created_at) FROM flow_scans").fetchone()[0]
        unresolved = int(
            conn.execute("SELECT COUNT(*) FROM tracked_markets WHERE resolved = 0").fetchone()[0]
        )
        resolved = int(
            conn.execute("SELECT COUNT(*) FROM tracked_markets WHERE resolved = 1").fetchone()[0]
        )
        row = conn.execute(
            "SELECT deployed FROM capital_ledger WHERE id = ?", (_CAPITAL_LEDGER_ID,)
        ).fetchone()
        deployed = float(row["deployed"]) if row else 0.0
        return {
            "scans_total": scans_total,
            "last_scan_at": last_scan,
            "tracked_unresolved": unresolved,
            "tracked_resolved": resolved,
            "deployed": deployed,
        }
    finally:
        conn.close()


def export_feedback_cases() -> list[dict[str, Any]]:
    """Return eval-compatible JSONL objects: {id, text, expected}.

    Notes:
    - For feedback linked to a parse_id, we export the parse text.
    - For feedback submitted only with input_id, text may be null unless provided via parse linkage.
      (We intentionally keep this minimal; future iteration can store text on feedback submission.)
    """

    conn = _connect()
    try:
        init_db(conn)
        rows = conn.execute(
            "SELECT id, text, expected_json FROM feedback ORDER BY id ASC"
        ).fetchall()
        cases: list[dict[str, Any]] = []
        for r in rows:
            expected = json.loads(r["expected_json"]) if r["expected_json"] else {}
            text = r["text"]
            if not text:
                # Skip cases we cannot export into eval harness.
                continue
            cases.append({"id": f"feedback-{r['id']}", "text": text, "expected": expected})
        return cases
    finally:
        conn.close()
