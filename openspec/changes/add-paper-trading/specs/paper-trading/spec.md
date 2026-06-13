# Capability: Paper Trading Replay

## ADDED Requirements

### Requirement: Flat-stake replay
The system SHALL simulate a flat-stake paper trade for every qualifying resolved signal (latest scan before resolution; non-null dominant side; YES/NO outcome; usable implied price strictly between 0 and 1), in resolution-time order. Each trade stakes `PAPER_STAKE` (default 100) on the dominant side at the scan-time implied price. Winning trades SHALL earn `stake × (1/price − 1) × (1 − PAPER_FEE)` (default fee 0.02); losing trades SHALL lose the stake.

#### Scenario: Win at even odds
- **WHEN** a HIGH-tier signal entered at implied price 0.5 resolves on the dominant side with fee 0
- **THEN** the trade PnL is +100.0 for a 100 stake

#### Scenario: Fee haircut on winnings
- **WHEN** the same trade settles with `PAPER_FEE=0.02`
- **THEN** the trade PnL is +98.0

#### Scenario: Loss
- **WHEN** a signal resolves against the dominant side
- **THEN** the trade PnL is −100.0 regardless of price

#### Scenario: Unusable price excluded
- **WHEN** a qualifying resolution has no stored scan price
- **THEN** no paper trade is simulated for it

### Requirement: Paper KPIs in dashboard data
`GET /dashboard/data` SHALL include a `paper` section with, per tier and overall: `trades`, `wins`, `win_rate`, `staked`, `pnl`, `roi` (pnl/staked), plus `max_drawdown` on the overall equity and an equity `curve` (one point per trade: cumulative overall and HIGH equity). The dashboard page SHALL render the equity curve and a paper-PnL KPI card.

#### Scenario: Payload shape
- **WHEN** two qualifying markets have resolved
- **THEN** `paper.curve` has two points and `paper.overall.trades == 2`

#### Scenario: No resolutions yet
- **WHEN** no scanned market has resolved
- **THEN** `paper.overall.trades == 0` and `paper.curve` is empty
