# Change: Add in-process scheduler with Telegram alerts (replaces n8n for wallet-flow)

## Why

The wallet-flow runbook currently depends on importing an n8n workflow to schedule scans and send alerts. That is a second system to install, configure, and keep running for what amounts to two timers and one HTTP POST. Building the scheduler into the service means: start one process, get scans, resolution polling, and Telegram alerts — and deployment to a VPS later is "run the same process elsewhere".

## What Changes

- **`app-scheduler` capability (ADDED)**:
  - New module `src/crypto_news_parser/scheduler.py`: async background loops started via FastAPI lifespan when `SCHEDULER_ENABLE=1` (default: disabled — no behaviour change for existing deployments).
  - Flow-scan loop: every `SCAN_INTERVAL_HOURS` (default 4), runs the same code path as `POST /flow-scan` with `SCAN_TOP_N` (default 20) and `SCAN_MAX_WALLETS` (default 300).
  - Resolution-poll loop: every `POLL_INTERVAL_MINUTES` (default 15), runs the `POST /poll-resolutions` code path (skipped when persistence is disabled).
  - Telegram alerts: markets at/above `ALERT_MIN_TIER` (default HIGH) send a message via the Bot API using `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`; alerting is a no-op when unconfigured.
  - Failure isolation: an exception in one cycle is logged and the loop continues.
- **`main.py` (MODIFIED)**: route bodies for `/flow-scan` and `/poll-resolutions` extracted into shared `do_flow_scan()` / `do_poll_resolutions()` functions used by both routes and scheduler (no contract change).
- **n8n (REMOVED file)**: `n8n/flow-scan-workflow.json` deleted before anyone imported it; the predict-market-risk n8n workflow is untouched.

## Impact

- Affected specs: `app-scheduler` (new)
- Affected code: `scheduler.py` (new), `main.py` (lifespan + extraction), `tests/test_scheduler.py` (new), README + METHODOLOGY runbook updates
- **Non-breaking**: scheduler is opt-in via `SCHEDULER_ENABLE=1`; all endpoints unchanged.
- **Non-goals**: scheduling for the news-signal n8n pipeline; deployment changes; alert channels other than Telegram.
