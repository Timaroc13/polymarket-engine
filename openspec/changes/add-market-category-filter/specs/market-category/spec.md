# Capability: Market Category Filtering

## ADDED Requirements

### Requirement: Category classification
The system SHALL classify each Polymarket market into exactly one of a fixed set of categories (`crypto`, `sports`, `macro`, `politics`, `geopolitics`, `tech`, `entertainment`, `other`) deterministically from the market's tags and question/slug text.

#### Scenario: Crypto market
- **WHEN** a market question mentions Bitcoin, Ethereum, a token, or another crypto keyword
- **THEN** `classify_category` returns `crypto`

#### Scenario: Sports market
- **WHEN** a market question matches a team-vs-team, over/under, or league pattern
- **THEN** `classify_category` returns `sports`

#### Scenario: Unmatched market
- **WHEN** a market matches no category keywords
- **THEN** `classify_category` returns `other`

### Requirement: Category-filtered scanning
`POST /flow-scan` SHALL only analyze markets whose category is in the request/env-configured allow-list (default `crypto`), and SHALL source enough candidate markets to fill `top_n` after filtering. The default resolution window (`max_days`) SHALL be 30 to accommodate crypto resolution horizons.

#### Scenario: Default crypto-only scan
- **WHEN** a scan runs with the default configuration
- **THEN** every analyzed market is classified `crypto`
- **AND** markets of other categories are excluded before trade/wallet fetching

#### Scenario: Explicit multi-category allow-list
- **WHEN** the request sets `categories` to `["crypto", "tech"]`
- **THEN** only markets classified `crypto` or `tech` are analyzed

### Requirement: Category tagging and breakdown
Each stored scan SHALL record its `category`, and each `FlowMarketResult` SHALL include a `category` field. `GET /dashboard/data` SHALL include a per-category breakdown reporting, for each category present, the resolved `n`, win rate, lift, and paper PnL.

#### Scenario: Scan row tagged
- **WHEN** a crypto market is scanned with persistence enabled
- **THEN** its `flow_scans` row has `category = "crypto"`

#### Scenario: Per-category dashboard data
- **WHEN** resolved markets span multiple categories
- **THEN** `GET /dashboard/data` returns one breakdown entry per category with its own n, win rate, lift, and paper PnL

### Requirement: Clean-slate reset
The system SHALL provide a reset operation that archives the current database (a timestamped copy) before clearing `flow_scans` and `tracked_markets`, so calibration can restart without losing the prior data.

#### Scenario: Reset archives then clears
- **WHEN** the reset operation runs against a populated database
- **THEN** a timestamped archive copy of the database is created
- **AND** afterwards `flow_scans` and `tracked_markets` contain no rows
