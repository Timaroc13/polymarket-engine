# Design: add-wallet-flow-signal

## Context

The detector exists as a standalone script (`polymarket_detector.py` + `verify_market.py`) in `C:\Users\oocta\Documents\Poly_Wallets`. It fetches markets from the Gamma API, trades from the Data API (`takerOnly=true`, top ~3000 recent trades per market, ≥$50), reconstructs per-wallet net USDC positions, flags "new wallets" (first trade ≤14 days ago AND exactly 1 market traded), and scores each market 0–100 on dominant-side new-wallet count/capital, a 7d/30d volume burst, and count dominance. Tiers: HIGH ≥70, MEDIUM ≥40, else LOW.

The parser service (this repo) has SQLite persistence, a `tracked_markets` table resolved by `POST /poll-resolutions` (called on schedule by n8n), and API-key auth. Today the detector's output is never joined with outcomes, so the signal is unvalidated.

## Goals / Non-Goals

- Goals:
  - Port detector logic into the service as testable, pure functions with thin HTTP fetchers.
  - Log every scan result with the market's implied probability at scan time.
  - Reuse the existing resolution pipeline (`tracked_markets` + `/poll-resolutions`) — no new poller.
  - Produce a calibration report: per tier, dominant-side win rate vs. average implied probability (lift).
- Non-Goals (later phases / backlog):
  - Wallet track-record (realized-edge) scoring with empirical-Bayes shrinkage (BL-14)
  - Maker fills and an incremental trade ledger that removes the 3000-trade snapshot cap (BL-15)
  - Funding-source clustering via Polygon on-chain data (BL-16)
  - Post-entry price drift tracking (BL-17)
  - Feeding the flow signal into `/signal` p_model or the `/risk` verdict — Phase 1 is observe-and-validate only.

## Decisions

- **Port, don't import**: detector logic is copied into `src/crypto_news_parser/wallet_flow.py` and becomes the source of truth (typed, unit-tested, ruff-formatted). The standalone script in Documents remains as-is for ad-hoc use; no shared-path import hacks across repos.
- **Sync HTTP in a threadpool**: fetchers use the stdlib/`requests`-style blocking pattern already used by `_fetch_polymarket_market` in `main.py`, executed via `asyncio.to_thread` (or `run_in_executor`) from the async route so a scan does not block the event loop. A scan of N markets is sequential with the script's existing pacing/retry behaviour (429 backoff, 400 → stop pagination).
- **Scoring is pure**: `detect(...)` takes already-fetched positions/metadata and returns the scored dict — identical semantics to the script so existing field names (`signal_score`, `risk_tier`, `dominant_side`, ...) carry over. This makes the scoring unit-testable without network access.
- **`p_market_at_scan`**: captured from the Gamma market object's `outcomePrices[0]` (YES price). Stored per scan row. For calibration, the dominant side's implied probability is `yes_price` when dominant side is YES, `1 − yes_price` when NO.
- **One row per (scan, market)**: repeated scans of the same market append rows (time series). The calibration report uses the **latest scan per market prior to resolution** — the most informed snapshot. Keeping all rows allows later analyses (e.g., earliest-tier lead time) without schema changes.
- **Resolution reuse**: every scanned market with persistence enabled is upserted into `tracked_markets` (ignore-if-exists, consistent with the unique `condition_id` constraint). `POST /poll-resolutions` already marks them resolved with an outcome; calibration joins `flow_scans` to `tracked_markets` on `condition_id`.
- **Calibration math**: per tier (and overall), report `n` (resolved markets), `wins` (dominant side matched outcome), `win_rate`, `avg_implied` (mean dominant-side implied probability at scan), and `lift = win_rate − avg_implied`. Markets whose dominant side is null (no new-wallet activity) are excluded from win-rate math but counted separately.
- **Endpoint shape**: `POST /flow-scan` (not GET) because it triggers external API calls and writes; body carries filters (`top_n`, `max_days`, `min_liquidity`, optional `condition_id`). `GET /flow-calibration` is read-only.

## Risks / Trade-offs

- **Scan latency**: a 20-market scan makes hundreds of Data API calls (trades pagination + wallet metadata) and can take minutes. Mitigation: n8n calls it on a schedule and doesn't need a fast response; document the expected duration; cap `top_n` at 50.
- **Data API pagination cap (~3000 trades)**: high-volume markets get only a recent window, so early accumulation may be missed. Accepted for Phase 1 (same as the script); fixed properly by the incremental ledger (BL-15).
- **Polymarket API drift**: field names (`outcomePrices`, `volume1wk`) are not formally versioned. Mitigation: defensive parsing (`_safe_float`, fallbacks) carried over from the script; fetchers isolated in one module.
- **Outcome mapping**: `tracked_markets.outcome` stores the resolved outcome label; calibration must map it to YES/NO robustly (case-insensitive compare). Markets with unparseable outcomes are excluded and counted in an `excluded` field.
- **Small samples early**: lift on n<30 is noise. The report includes `n` so the consumer (the user) can judge; no significance testing in Phase 1.

## Migration Plan

Additive only. New table `flow_scans` is created by `init_db` on first use; no changes to existing tables or endpoints. Rollback = drop the table and remove the routes.

## Open Questions

- None blocking. Whether `flow_tier` should later gate `/risk` (veto betting against HIGH-tier markets) is deliberately deferred until calibration data exists.
