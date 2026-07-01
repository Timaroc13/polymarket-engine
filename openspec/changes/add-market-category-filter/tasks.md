# Tasks: add-market-category-filter

## 1. Classification + scanner

- [x] 1.1 `wallet_flow.classify_category(market)` — regex over Gamma tags + question/slug → fixed category set (port/refine the analysis-script regexes; sports/crypto/macro/politics/geopolitics/tech/entertainment/other)
- [x] 1.2 `fetch_top_markets(..., categories, max_days=30)` — fetch a larger candidate pool, classify, keep only allow-listed categories until `top_n`; verify live that crypto markets are actually retrieved
- [x] 1.3 `analyze_market` attaches `category` to the result; `run_scan` threads `categories` through

## 2. Storage + models

- [x] 2.1 `flow_scans.category` column (migration-safe add in `init_db`); `store_flow_scan` persists it
- [x] 2.2 `get_category_breakdown()` — per-category n / win_rate / lift / paper_pnl over resolved markets (reuse `_calibration_rows` + `_qualify_row` + paper math)
- [x] 2.3 `FlowMarketResult.category`; `FlowScanRequest.categories` (default None → env) and `max_days` default 30

## 3. Routes + dashboard + scheduler

- [x] 3.1 `do_flow_scan` resolves categories from request or `SCAN_CATEGORIES` env (default `crypto`); `SCAN_MAX_DAYS` default 30 in scheduler
- [x] 3.2 `dashboard.build_dashboard_data` adds `by_category`; dashboard HTML renders a per-category table

## 4. Reset + tests

- [x] 4.1 `scripts/reset_flow_data.py` — copy DB to `data_archive_<ts>.sqlite3`, then clear `flow_scans` + `tracked_markets`
- [x] 4.2 `tests/test_category.py` — classifier cases; scan filters to allow-list (mocked fetch); flow_scans stores category; per-category breakdown math; reset archives+clears (tmp DB)
- [x] 4.3 `ruff check .`, full pytest, `openspec validate add-market-category-filter --strict --no-interactive`

## 5. Cut over

- [x] 5.1 Run the reset on the live DB (archive the 669-market run); set `SCAN_CATEGORIES=crypto`; leave tasks disabled until user re-enables
- [x] 5.2 README + METHODOLOGY: document category filter, env vars, per-category dashboard, and the reset procedure
