# Change: Add wallet-flow signal with validation harness (Phase 1)

## Why

`C:\Users\oocta\Documents\Poly_Wallets\polymarket_detector.py` detects informed trading on Polymarket by flagging new wallets (≤14 days old, exactly 1 market traded) deploying capital on one side of a market. It works as a standalone snapshot script, but it has no outcome-based validation: score weights and tier cutoffs are hand-tuned guesses, results are written to ad-hoc JSON files, and there is no way to answer the only question that matters — *does the dominant side of HIGH-tier markets resolve YES more often than the market price implied?*

The parser service already has every piece of plumbing the detector lacks: SQLite persistence, `tracked_markets` + `POST /poll-resolutions` for resolution outcomes, API-key auth, and an n8n scheduler. Integrating the detector as a first-class capability turns it from a one-shot script into a continuously validated signal, and lays the foundation for later phases (wallet track-record scoring, maker-fill coverage, funding-source clustering).

## What Changes

- **`wallet-flow` capability (ADDED)**:
  - New module `src/crypto_news_parser/wallet_flow.py` porting the detector's position reconstruction and scoring logic (pure functions) plus Gamma/Data API fetchers.
  - `POST /flow-scan`: scan top active markets (or one market by `condition_id`) and return per-market flow analysis (score, tier, dominant side, new-wallet stats, implied probability at scan time).
  - When persistence is enabled, each scanned market is stored in a new `flow_scans` table and auto-registered in `tracked_markets` so the existing `POST /poll-resolutions` flow resolves it.
  - `GET /flow-calibration`: joins stored scans with resolved outcomes and reports, per tier, the sample count, dominant-side win rate, average implied probability at scan, and lift (win rate − implied probability).
- **Backlog (ADDED items)**: later phases recorded as BL-14 (wallet track-record edge scoring), BL-15 (maker fills + incremental trade ledger), BL-16 (funding-source wallet clustering), BL-17 (post-entry price drift metric).

**Non-breaking**: all changes are additive. Existing endpoints, schemas, and the standalone script are untouched.

## Impact

- Affected specs: `wallet-flow` (new capability)
- Affected code:
  - `src/crypto_news_parser/wallet_flow.py` (new): detector port — fetchers, position reconstruction, scoring
  - `src/crypto_news_parser/storage.py`: `flow_scans` table, insert/query helpers, calibration aggregation
  - `src/crypto_news_parser/models.py`: `FlowScanRequest`, `FlowScanResponse`, `FlowCalibrationResponse` models
  - `src/crypto_news_parser/main.py`: `POST /flow-scan`, `GET /flow-calibration` routes
  - `tests/`: new `test_wallet_flow.py` (scoring unit tests, storage, calibration math, API with mocked fetchers)
  - `openspec/backlog.md`: BL-14..BL-17
  - `README.md`: endpoint docs + n8n scheduling note
- **Non-goals** (deferred to later phases): veteran wallet track-record scoring; maker-fill ingestion; incremental trade ledger; funding-graph clustering; post-entry drift; any change to `/signal` or `/risk` verdict logic — the flow signal is observe-and-validate only in Phase 1.
