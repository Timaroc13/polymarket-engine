# Capability: Wallet Flow Signal (Phase 1)

## ADDED Requirements

### Requirement: Flow scan endpoint
The system SHALL expose `POST /flow-scan` which scans active Polymarket markets for informed-trading flow and returns a schema-valid list of per-market flow analyses. The request body MAY include `top_n` (default 20, max 50), `max_days` (default 7), `min_liquidity` (default 10000), a single `condition_id` which bypasses the scan filters, or `max_wallets` (default null = unlimited) which caps wallet-metadata lookups per market to the top-N wallets by position size — wallets beyond the cap are treated as not-new. Each market result SHALL include `market_id`, `market_question`, `signal_score` (integer 0–100), `risk_tier` (`LOW` | `MEDIUM` | `HIGH`), `dominant_side` (`YES` | `NO` | null), dominant-side new-wallet count and USDC totals, and `p_market_at_scan` (the market's YES implied probability at scan time, float in [0.0, 1.0], null when unavailable).

#### Scenario: Default scan
- **WHEN** the client submits `POST /flow-scan` with an empty JSON body
- **THEN** the API returns HTTP 200 with a list of market flow analyses for active markets matching the default filters
- **AND** each entry includes `signal_score`, `risk_tier`, `dominant_side`, and `p_market_at_scan`

#### Scenario: Single market by condition_id
- **WHEN** the client submits `POST /flow-scan` with `{"condition_id": "0xabc..."}`
- **THEN** the API returns HTTP 200 with exactly one flow analysis for that market, bypassing the scan filters

#### Scenario: Wallet metadata lookups capped
- **WHEN** the client submits `{"max_wallets": 100}` and a market has 900 wallets with open positions
- **THEN** wallet metadata is fetched only for the 100 wallets with the largest total position size
- **AND** the remaining wallets are excluded from new-wallet aggregates

#### Scenario: API key enforcement
- **WHEN** `API_KEY` is configured and the request lacks a valid `Authorization: Bearer` header
- **THEN** the API returns HTTP 401 with the documented error schema

### Requirement: Flow scoring
The system SHALL score each market from reconstructed per-wallet net positions using the new-wallet informed-flow heuristic. A wallet is "new" when its first trade on Polymarket occurred at most 14 days before scan time AND it has traded exactly one market. The dominant side is the side (YES/NO) with the larger new-wallet USDC total. The score SHALL be the sum of: dominant-side wallet count tiers (≥3: +10, ≥10: +10, ≥20: +10), dominant-side capital tiers (≥$5k: +15, ≥$20k: +15, ≥$100k: +10), volume-burst tiers (week/month volume ≥0.30: +10, ≥0.60: +10), and count-dominance tiers (dominant-side share of new wallets ≥0.60: +5, ≥0.80: +5). Risk tier SHALL be `HIGH` when score ≥ 70, `MEDIUM` when score ≥ 40, otherwise `LOW`.

#### Scenario: Directional new-wallet flow scores against the dominant side
- **WHEN** 12 new wallets hold $25,000 net on YES and 1 new wallet holds $500 on NO, with week/month volume burst of 0.65
- **THEN** `dominant_side = "YES"` and the score includes wallet-count points (+20), capital points (+30), burst points (+20), and count-dominance points (+10)
- **AND** `risk_tier = "HIGH"`

#### Scenario: No new-wallet activity
- **WHEN** no wallet in the market qualifies as new
- **THEN** `signal_score = 0`, `risk_tier = "LOW"`, and `dominant_side` is null

#### Scenario: Hedged bilateral flow earns no count-dominance points
- **WHEN** new-wallet counts are split 50/50 between YES and NO
- **THEN** no count-dominance points are awarded

### Requirement: Scan persistence and resolution tracking
When persistence is enabled (`ENABLE_PERSISTENCE=1`), the system SHALL store one row per scanned market in a `flow_scans` table — including `condition_id`, `question`, `signal_score`, `risk_tier`, `dominant_side`, dominant-side USDC, `p_market_at_scan`, and the full result JSON — and SHALL register each scanned market in `tracked_markets` (ignoring duplicates) so the existing `POST /poll-resolutions` flow resolves it. When persistence is disabled, the scan SHALL still return results without storing anything.

#### Scenario: Persistence enabled
- **WHEN** `ENABLE_PERSISTENCE=1` and a scan analyzes a market not yet tracked
- **THEN** a `flow_scans` row is inserted for the market
- **AND** the market's `condition_id` is present in `tracked_markets` afterwards

#### Scenario: Repeated scans append history
- **WHEN** the same market is scanned twice on different days with persistence enabled
- **THEN** two `flow_scans` rows exist for that `condition_id`

#### Scenario: Persistence disabled
- **WHEN** `ENABLE_PERSISTENCE` is unset
- **THEN** `POST /flow-scan` returns HTTP 200 with results and no database rows are created

### Requirement: Flow calibration report
The system SHALL expose `GET /flow-calibration` which joins stored flow scans with resolved market outcomes and returns, per risk tier and overall: `n` (resolved markets with a non-null dominant side, using the latest scan per market prior to resolution), `wins` (count where the dominant side matched the resolved outcome), `win_rate`, `avg_implied` (mean dominant-side implied probability at scan time), and `lift` (`win_rate − avg_implied`). Markets with unresolvable or unparseable outcomes SHALL be excluded from the math and reported in an `excluded` count. The endpoint SHALL return HTTP 400 with code `PERSISTENCE_DISABLED` when persistence is disabled (consistent with `/track-market` and `/poll-resolutions`).

#### Scenario: Calibration over resolved markets
- **WHEN** persistence is enabled and 10 scanned markets have resolved, 7 of which resolved on their latest scan's dominant side with an average dominant-side implied probability of 0.55
- **THEN** the overall report includes `n = 10`, `wins = 7`, `win_rate = 0.7`, `avg_implied = 0.55`, and `lift = 0.15` (within rounding)

#### Scenario: No resolved markets yet
- **WHEN** persistence is enabled but no scanned market has resolved
- **THEN** the API returns HTTP 200 with `n = 0` per tier and null `win_rate`, `avg_implied`, and `lift`

#### Scenario: Persistence disabled
- **WHEN** `ENABLE_PERSISTENCE` is unset
- **THEN** `GET /flow-calibration` returns HTTP 400 with code `PERSISTENCE_DISABLED`
