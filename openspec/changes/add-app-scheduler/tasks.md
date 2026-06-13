# Tasks: add-app-scheduler

## 1. Implementation

- [x] 1.1 Extract `do_flow_scan(req)` and `do_poll_resolutions()` from the route bodies in `main.py` (routes keep auth/persistence guards; behaviour unchanged)
- [x] 1.2 Create `scheduler.py`: env config, `format_alert`, Telegram sender (stdlib urllib, no-op when unconfigured), `scan_once`/`poll_once`, resilient `_loop`, `start(scan_fn, poll_fn)`
- [x] 1.3 Wire FastAPI lifespan in `main.py`: start tasks when `SCHEDULER_ENABLE=1`, cancel them on shutdown
- [x] 1.4 Delete `n8n/flow-scan-workflow.json`

## 2. Tests

- [x] 2.1 `tests/test_scheduler.py`: alert formatting; tier filtering (default HIGH, `ALERT_MIN_TIER=MEDIUM`); Telegram no-op without config; loop survives a failing cycle; lifespan spawns tasks only when enabled

## 3. Docs & validation

- [x] 3.1 README: scheduler env vars; remove n8n import instructions for wallet-flow
- [x] 3.2 METHODOLOGY.md: runbook §5 — one-process operation; n8n no longer required for wallet-flow
- [x] 3.3 `ruff check .`, full pytest, `openspec validate add-app-scheduler --strict --no-interactive`
