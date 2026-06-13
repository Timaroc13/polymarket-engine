# Change: Add paper-trading replay to the dashboard

## Why

Calibration lift measures probability edge; it cannot tell whether the signal makes *money* — wins at bad prices can produce positive lift and negative PnL. A flat-stake paper replay over the data the engine already logs (side, tier, price at scan, outcome) adds the money lens with zero new data collection, works retroactively, and exercises the judgement needed before Gate 2.

## What Changes

- **`paper-trading` capability (ADDED)**:
  - Pure replay in new `paper.py`: a virtual flat stake (`PAPER_STAKE`, default $100) on the dominant side of every qualifying resolved signal at its scan-time implied price, with a fee/slippage haircut on winnings (`PAPER_FEE`, default 2%).
  - Per tier and overall: trades, wins, win rate, total staked, PnL, ROI; max drawdown on the overall equity curve.
  - Equity curve (overall + HIGH) added to `GET /dashboard/data` (`paper` section) and rendered on `/dashboard` with a paper-PnL KPI card.
  - Entries lacking a usable price are excluded (consistent with calibration exclusions).
- **Deliberately out of scope**: Kelly-sized paper bets (the flow signal has no `p_model`; tier→probability mapping would be fake precision — revisit with BL-14).

## Impact

- Affected specs: `paper-trading` (new)
- Affected code: `paper.py` (new), `storage.py` (`get_paper_entries()` reusing the calibration join), `dashboard.py` (data + HTML), `tests/test_paper.py`
- **Non-breaking**: additive `paper` key in the dashboard payload; no endpoint changes.
