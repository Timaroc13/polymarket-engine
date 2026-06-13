# Capability: KPI Dashboard

## ADDED Requirements

### Requirement: Dashboard page
The system SHALL serve `GET /dashboard` returning a self-contained HTML page (no build step, no authentication) that displays wallet-flow KPIs and auto-refreshes its data periodically.

#### Scenario: Page loads
- **WHEN** a browser requests `GET /dashboard`
- **THEN** the API returns HTTP 200 with `text/html` content

### Requirement: Dashboard data endpoint
The system SHALL serve `GET /dashboard/data` (no authentication) returning a JSON payload containing: the flow calibration report, a lift-evolution timeline with one point per resolved market (cumulative `n`, `win_rate`, `avg_implied`, `lift` for overall and HIGH tier, ordered by resolution time), the most recent scans (market, tier, score, dominant side, `p_market_at_scan`, scan time), and operational stats (total scans, last scan time, tracked unresolved/resolved counts, deployed capital). The endpoint SHALL return HTTP 400 with code `PERSISTENCE_DISABLED` when persistence is disabled.

#### Scenario: Data with resolutions
- **WHEN** persistence is enabled and two scanned markets have resolved
- **THEN** the response timeline contains two points with cumulative calibration values
- **AND** the stats include the resolved and unresolved counts

#### Scenario: Persistence disabled
- **WHEN** `ENABLE_PERSISTENCE` is unset
- **THEN** `GET /dashboard/data` returns HTTP 400 with code `PERSISTENCE_DISABLED`

### Requirement: Lift evolution semantics
Each timeline point SHALL be computed from the latest scan per market prior to its resolution (consistent with `GET /flow-calibration`), accumulated in resolution-time order, so the dashboard shows how lift converges as the sample grows. Markets with null dominant side or unparseable outcomes SHALL be excluded from timeline math.

#### Scenario: Cumulative lift
- **WHEN** three HIGH-tier markets resolve as win, loss, win with dominant-side implied probabilities 0.5 each
- **THEN** the HIGH timeline lifts after each resolution are +0.5, 0.0, and approximately +0.1667
