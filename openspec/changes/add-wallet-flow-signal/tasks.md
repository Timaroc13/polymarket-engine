# Tasks: add-wallet-flow-signal

## 1. Wallet flow module (port detector)

- [x] 1.1 Create `src/crypto_news_parser/wallet_flow.py` with pure logic ported from `polymarket_detector.py`:
  - `build_positions_from_trades(trades) -> list[dict]` (per-wallet, per-outcome net USDC; drop net ≤ 0)
  - `detect(...) -> dict` (new-wallet flagging, dominant side, score components, tier) — identical semantics and field names to the script
  - Constants: `NEW_WALLET_AGE_SECONDS = 14d`, `MIN_TRADE_USDC = 50`, trades page size 500, max offset 3000
- [x] 1.2 Add HTTP fetchers in the same module (blocking, with 429 backoff and 400 → stop-pagination, matching the script):
  - `fetch_top_markets(top_n, max_days, min_liquidity)` (Gamma)
  - `fetch_market_by_condition_id(condition_id)` (Gamma)
  - `fetch_trades_for_market(condition_id)` (Data API, takerOnly, ≥$50 filter)
  - `fetch_wallet_metadata(wallet, cache)` (Data API, early bailout at ≥2 distinct markets; cache passed in, not module-global)
- [x] 1.3 Add `analyze_market(market) -> dict` orchestration and extract `p_market_at_scan` from the Gamma market's `outcomePrices[0]` (null-safe)

- [x] 1.4 Add `max_wallets` cap (request field + `run_scan`/`analyze_market` plumbing): metadata lookups limited to top-N wallets by position size; default None = full fidelity

## 2. Storage

- [x] 2.1 Add `flow_scans` table to `init_db` in `storage.py`: id, created_at, condition_id, question, signal_score, risk_tier, dominant_side, dominant_side_usdc, p_market_at_scan, result_json
- [x] 2.2 Add `store_flow_scan(...)` insert helper
- [x] 2.3 Add `track_market_if_new(condition_id, question)` (INSERT OR IGNORE into `tracked_markets`)
- [x] 2.4 Add `get_flow_calibration() -> dict`: latest scan per condition_id joined to resolved `tracked_markets`; per-tier and overall n / wins / win_rate / avg_implied / lift; `excluded` count for unparseable outcomes (case-insensitive YES/NO mapping)

## 3. Models and routes

- [x] 3.1 Add `FlowScanRequest` (top_n ≤ 50, max_days, min_liquidity, condition_id), `FlowMarketResult`, `FlowScanResponse`, `FlowCalibrationResponse` to `models.py`
- [x] 3.2 Add `POST /flow-scan` route in `main.py`: API-key check, run scan via `asyncio.to_thread`, store rows + track markets when persistence enabled, return results
- [x] 3.3 Add `GET /flow-calibration` route: API-key check, 400 PERSISTENCE_DISABLED when persistence disabled, otherwise return report

## 4. Tests

- [x] 4.1 `tests/test_wallet_flow.py` — scoring unit tests on synthetic positions: HIGH-tier directional case, no-new-wallet case, 50/50 hedge (no dominance points), burst tiers, tier cutoffs at 40/70
- [x] 4.2 Position reconstruction tests: BUY/SELL netting, net ≤ 0 dropped, dust/invalid rows skipped
- [x] 4.3 Storage tests (tmp DB): flow_scans insert, repeated scans append, track_market_if_new idempotency
- [x] 4.4 Calibration tests: known scan+resolution fixtures → expected n/wins/win_rate/avg_implied/lift; latest-scan-per-market selection; excluded outcomes
- [x] 4.5 API tests with mocked fetchers: default scan 200, condition_id path, 401 without API key, persistence on/off behaviour, calibration 400 without persistence

## 5. Documentation and backlog

- [x] 5.1 README: document `POST /flow-scan` and `GET /flow-calibration` (including expected scan duration and n8n scheduling note)
- [x] 5.2 `openspec/backlog.md`: add BL-14 (wallet track-record edge scoring with shrinkage + persistence test), BL-15 (maker fills + incremental trade ledger removing the 3000-trade cap), BL-16 (funding-source wallet clustering via Polygon data), BL-17 (post-entry price drift metric)

## 6. Validation

- [x] 6.1 Run `ruff check` and full `pytest` suite (142 passed; repo-wide ruff clean)
- [x] 6.2 Run `openspec validate add-wallet-flow-signal --strict --no-interactive` and resolve all issues
