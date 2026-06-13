# Tasks: add-paper-trading

## 1. Implementation

- [x] 1.1 `storage.get_paper_entries()`: qualifying calibration rows → {win, price, tier, resolved_at}, excluding unusable prices
- [x] 1.2 `paper.py`: pure `simulate_paper_trading(entries, stake, fee)` — per-tier/overall buckets, equity curve, max drawdown; `get_paper_report()` reading PAPER_STAKE/PAPER_FEE env
- [x] 1.3 `dashboard.py`: add `paper` to `build_dashboard_data()`; equity-curve chart + paper KPI card in the HTML

## 2. Tests & validation

- [x] 2.1 `tests/test_paper.py`: spec scenarios (win/fee/loss/excluded price), ROI + max drawdown math, tier buckets, dashboard payload shape
- [x] 2.2 `ruff check .`, full pytest, `openspec validate add-paper-trading --strict --no-interactive`; restart server, verify dashboard renders
