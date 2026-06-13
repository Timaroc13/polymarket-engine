# Capability: In-Process Scheduler

## ADDED Requirements

### Requirement: Opt-in background scheduling
When `SCHEDULER_ENABLE=1`, the service SHALL start background tasks at application startup that (a) run a flow scan every `SCAN_INTERVAL_HOURS` (default 4) using `SCAN_TOP_N` (default 20) and `SCAN_MAX_WALLETS` (default 300), and (b) poll market resolutions every `POLL_INTERVAL_MINUTES` (default 15) when persistence is enabled. When `SCHEDULER_ENABLE` is unset or `0`, no background tasks SHALL start and service behaviour is unchanged.

#### Scenario: Scheduler enabled
- **WHEN** the app starts with `SCHEDULER_ENABLE=1`
- **THEN** two background tasks are running (scan loop and poll loop)

#### Scenario: Scheduler disabled by default
- **WHEN** the app starts without `SCHEDULER_ENABLE`
- **THEN** no scheduler tasks are started

### Requirement: Telegram tier alerts
After each scheduled scan, the system SHALL send one Telegram message per market whose `risk_tier` is at or above `ALERT_MIN_TIER` (default `HIGH`), using `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. When either variable is unset, alerting SHALL be a silent no-op and the scan still completes.

#### Scenario: HIGH-tier market alerts
- **WHEN** a scheduled scan returns one HIGH and one LOW market with Telegram configured
- **THEN** exactly one alert is sent, for the HIGH market

#### Scenario: Lower threshold includes MEDIUM
- **WHEN** `ALERT_MIN_TIER=MEDIUM` and a scan returns one MEDIUM market
- **THEN** an alert is sent for it

#### Scenario: Telegram unconfigured
- **WHEN** `TELEGRAM_BOT_TOKEN` is unset
- **THEN** no message is attempted and no error is raised

### Requirement: Failure isolation
An exception raised during one scheduled cycle SHALL be logged and SHALL NOT terminate the loop; the next cycle runs on schedule.

#### Scenario: Scan cycle failure
- **WHEN** a scan cycle raises a network error
- **THEN** the error is logged and the following cycle still executes
