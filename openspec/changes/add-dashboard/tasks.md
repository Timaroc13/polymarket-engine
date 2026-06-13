# Tasks: add-dashboard

## 1. Storage

- [x] 1.1 Extract the calibration join into `_calibration_rows(conn)` (shared with `get_flow_calibration`, adds `resolved_at`)
- [x] 1.2 `get_calibration_timeline()`: cumulative n/wins/win_rate/avg_implied/lift per resolution (overall + HIGH), resolution-time order
- [x] 1.3 `get_recent_scans(limit=50)` and `get_dashboard_stats()` (scan count, last scan ts, tracked unresolved/resolved, deployed)

## 2. Dashboard

- [x] 2.1 `dashboard.py`: `build_dashboard_data()` JSON assembly + `DASHBOARD_HTML` (Chart.js CDN, dark theme, KPI cards, lift chart, tier doughnut, recent-scans table, 60s refresh)
- [x] 2.2 Routes in `main.py`: `GET /dashboard` (HTML), `GET /dashboard/data` (JSON, 400 without persistence); no API key on either

## 3. Tests & docs

- [x] 3.1 `tests/test_dashboard.py`: timeline cumulative math (spec scenario), recent-scan ordering/limit, data endpoint with fixtures, 400 without persistence, /dashboard returns HTML
- [x] 3.2 README + METHODOLOGY: weekly check = open `http://localhost:8000/dashboard`
- [x] 3.3 `ruff check .`, full pytest, `openspec validate add-dashboard --strict --no-interactive`; restart the server task and verify the page live
