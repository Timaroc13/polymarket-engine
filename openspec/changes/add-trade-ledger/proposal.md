# Change: Add incremental trade ledger with maker fills (wallet-flow Phase 2, BL-15)

## Why

Phase 1's `/flow-scan` re-fetches up to 3,000 recent taker trades per market on every scan. This has three structural blind spots:

1. **The ~3,000-trade pagination cap**: on high-volume markets the window covers only the most recent hours, so early quiet accumulation — the most informative flow — is invisible.
2. **Taker-only**: informed traders who accumulate via passive limit orders (the cheap way to build size without moving price) never appear in `takerOnly=true` results.
3. **No history**: every scan starts cold, so per-wallet behaviour over time (needed for BL-14 track-record scoring and BL-17 drift) cannot be computed.

A local incremental ledger fixes all three: fetch only trades newer than the last stored one per market, include maker fills, and keep everything in SQLite. It is the substrate both later phases depend on.

## What Changes

- **`trade-ledger` capability (ADDED)**:
  - New `trades` table (dedupe key: transaction hash + event index) and `ledger_markets` cursor table (per-market latest stored timestamp/offset).
  - Ingestion function: for each tracked/scanned market, page the Data API from the newest stored trade forward (`takerOnly=false`), insert-or-ignore.
  - `/flow-scan` reads positions from the ledger when the market has ledger coverage, falling back to the live snapshot fetch otherwise (Phase 1 behaviour preserved).
  - `POST /ledger-sync` endpoint (scheduler-friendly): syncs the ledger for all unresolved tracked markets and returns per-market new-trade counts.
- **`wallet-flow` capability (MODIFIED)**: position reconstruction documents the ledger-first source and the maker-fill inclusion; scoring semantics unchanged.

**Non-breaking**: all changes are additive; scans on un-synced markets behave exactly as Phase 1.

## Impact

- Affected specs: `trade-ledger` (new), `wallet-flow` (modified — position source)
- Affected code: `storage.py` (tables + queries), `wallet_flow.py` (ledger-first reconstruction), `main.py` (`/ledger-sync`), new tests, n8n (add a sync step before the 4-hour scan)
- **Open question for review**: Data API maker-fill semantics — confirm `takerOnly=false` returns both sides per trade and how to avoid double-counting a wallet that is both maker and taker in the same fill. Resolve in design.md before implementation.
- **Non-goals**: wallet track-record scoring (BL-14, next change), funding clustering (BL-16), drift metric (BL-17) — all become straightforward queries once this ledger exists.
