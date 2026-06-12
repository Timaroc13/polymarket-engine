# Capability: Trade Ledger (wallet-flow Phase 2)

## ADDED Requirements

### Requirement: Incremental trade ingestion
The system SHALL maintain a local SQLite ledger of Polymarket trades for tracked markets, ingesting incrementally (only trades newer than the last stored trade per market), including maker fills (`takerOnly=false`), and deduplicating on transaction hash + event index + wallet so repeated syncs are idempotent.

#### Scenario: Incremental sync
- **WHEN** a market with 500 stored trades is synced and 40 new trades exist upstream
- **THEN** exactly 40 rows are added and the market's cursor advances to the newest trade timestamp

#### Scenario: Idempotent re-sync
- **WHEN** the same market is synced twice with no new upstream trades
- **THEN** the second sync inserts zero rows

### Requirement: Ledger sync endpoint
The system SHALL expose `POST /ledger-sync` which syncs the ledger for all unresolved tracked markets and returns per-market new-trade counts. The endpoint SHALL enforce the API key when configured and SHALL return HTTP 400 with code `PERSISTENCE_DISABLED` when persistence is disabled.

#### Scenario: Scheduled sync
- **WHEN** the scheduler calls `POST /ledger-sync` with three unresolved tracked markets
- **THEN** the API returns HTTP 200 with a per-market list of newly ingested trade counts

### Requirement: Ledger-first position reconstruction
When a market has ledger coverage, `POST /flow-scan` SHALL reconstruct per-wallet positions from the ledger (full history, maker fills included) instead of the live 3,000-trade taker-only snapshot, and SHALL indicate the source via a `position_source` field (`ledger` | `snapshot`). Markets without coverage SHALL fall back to Phase 1 snapshot behaviour unchanged.

#### Scenario: Covered market uses ledger
- **WHEN** a scanned market has ledger rows
- **THEN** positions are built from all ledger trades and the result includes `position_source = "ledger"`

#### Scenario: Uncovered market falls back
- **WHEN** a scanned market has no ledger rows
- **THEN** the scan behaves exactly as Phase 1 and the result includes `position_source = "snapshot"`
