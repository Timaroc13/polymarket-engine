# Capability: Capital Ledger (multi-strategy)

## ADDED Requirements

### Requirement: Strategy-tagged reservations
The system SHALL accept an optional `strategy` string on `POST /risk` (default `"default"`) and store it on capital reservations made via `auto_reserve`, so that multiple trading strategies sharing one bankroll are individually attributable. Reservation idempotency by `bet_id` SHALL be preserved.

#### Scenario: Tagged reservation
- **WHEN** `POST /risk` is called with `auto_reserve=true`, `strategy="hk-temp"`, a `bet_id`, and the verdict is GO
- **THEN** the reserved amount is recorded against strategy `hk-temp`

#### Scenario: Untagged caller unchanged
- **WHEN** an existing caller omits `strategy`
- **THEN** behaviour is identical to today and the reservation is attributed to `"default"`

### Requirement: Per-strategy deployed breakdown
`GET /deployed` SHALL return the total deployed capital plus a per-strategy breakdown of active reservations, so any bot (or the user) can see whose positions consume the shared exposure budget.

#### Scenario: Breakdown across two strategies
- **WHEN** strategy `news-signal` has $300 reserved and `hk-temp` has $200 reserved
- **THEN** `GET /deployed` returns `deployed = 500` and a breakdown listing both strategies with their amounts

### Requirement: Release by bet id
`POST /deployed/release` SHALL accept an optional `bet_id`; when provided, the system SHALL release exactly that reservation's amount, idempotently (a second release of the same `bet_id` is a no-op). Amount-based release SHALL remain supported for backward compatibility.

#### Scenario: Idempotent release
- **WHEN** `/deployed/release` is called twice with the same `bet_id`
- **THEN** the reservation amount is subtracted exactly once
