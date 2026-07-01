# Change: Category-filtered scanning + clean-slate reset (crypto focus)

## Why

Empirical finding from the first 669-market run: **259 of 321 qualifying paper trades (81%) were sports**, which lost −$4,900 at zero lift, while only **6 were crypto** — the sole category with positive lift (+0.071) and positive PnL (+$65). The scanner picks "top-by-24h-volume, resolving ≤7 days," and that universe is structurally almost all sports/politics. So validation has been measuring the wrong markets, and the one category the detector was designed for (crypto: wallet-native users, real insider info) is starved to n=6.

Two structural causes: (1) no category filter, and (2) `max_days=7` excludes most crypto markets, which resolve monthly/EOY rather than weekly.

## What Changes

- **`market-category` capability (ADDED)**:
  - `classify_category(market)` — deterministic classification from Gamma tags + question/slug into a fixed set (`crypto`, `sports`, `macro`, `politics`, `geopolitics`, `tech`, `entertainment`, `other`).
  - Scanner filters to a configured allow-list (default `crypto`), sourcing enough markets to fill `top_n` after filtering, and uses a relaxed default window (`max_days` default 30) suited to crypto resolution horizons.
  - Each `flow_scans` row stores its `category`; `FlowMarketResult` gains a `category` field.
  - `GET /dashboard/data` and the dashboard page report a **per-category breakdown** (n, win rate, lift, paper PnL) so categories are compared directly.
  - `scripts/reset_flow_data.py` — archives the current DB (timestamped copy) then clears `flow_scans` + `tracked_markets` for a clean counter.
- **Scheduler env**: `SCAN_CATEGORIES` (default `crypto`), `SCAN_MAX_DAYS` (default 30).

## Impact

- Affected specs: `market-category` (new); touches `wallet-flow` scan behaviour (now category-filtered — noted, non-breaking to the API shape).
- Affected code: `wallet_flow.py` (classifier + filtered fetch), `storage.py` (category column + per-category query), `models.py` (`category` field, `FlowScanRequest.categories`/`max_days` default), `main.py` (`do_flow_scan` passes categories), `dashboard.py` (per-category panel), `scheduler.py` (env), `scripts/reset_flow_data.py`, tests.
- **Non-goals**: retuning the score; tech/other categories (crypto-only focus now, but tagging makes adding them later trivial); changing `/risk`.
