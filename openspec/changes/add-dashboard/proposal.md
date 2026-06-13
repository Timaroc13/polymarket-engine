# Change: Add localhost KPI dashboard

## Why

The validation phase produces numbers that currently live behind curl commands: calibration lift, gate progress, scan history. A single dashboard page served by the engine itself makes the weekly check a browser bookmark and shows how the signal evolves as resolutions accumulate — without adding any new process, framework, or build step.

## What Changes

- **`dashboard` capability (ADDED)**:
  - `GET /dashboard`: self-contained HTML page (vanilla JS + Chart.js CDN), dark theme, auto-refreshes every 60s.
  - `GET /dashboard/data`: one JSON payload — calibration report, lift-evolution timeline (cumulative lift recomputed at each resolution, overall + HIGH), recent scans, and operational stats (total scans, last scan time, tracked unresolved/resolved, deployed capital).
  - KPI cards: Gate-1 progress (HIGH n / 30), HIGH lift, HIGH win rate vs implied, deployed capital, last scan.
  - Charts: lift evolution line (does the edge stabilize as n grows?) and recent-scan tier distribution.
  - Table: latest scan results (market, tier, score, side, price at scan, days to expiry).
  - Both endpoints are read-only and exempt from the API key (localhost convenience; documented).
- **`storage.py` (MODIFIED)**: calibration join extracted into a shared helper; new `get_recent_scans(limit)`, `get_calibration_timeline()`, `get_dashboard_stats()` queries.

## Impact

- Affected specs: `dashboard` (new)
- Affected code: `dashboard.py` (new — HTML + data assembly), `storage.py` (queries), `main.py` (two GET routes), `tests/test_dashboard.py` (new), README/METHODOLOGY pointers
- **Non-breaking**: additive; no existing endpoint changes.
- **Non-goals**: auth/multi-user; historical snapshots beyond what `flow_scans` + `tracked_markets` already store; news-signal/`/risk` visualisation (can join later once those have calibration data).
