# Product Backlog

Items below are ranked by impact/effort ratio. Each item is a candidate for a future OpenSpec proposal.

## 🔴 High Priority

### ~~BL-01: Auto-Resolution Feedback Loop~~ ✅ DONE
Poll Polymarket API for `resolved: true` on tracked market slugs and auto-POST `/feedback` with the outcome.
Without this, there is no way to measure whether `p_model` is calibrated correctly over time.
- **Why**: Turns the pipeline from a black box into a learning system
- **Effort**: Medium — needs a separate n8n poller workflow + feedback endpoint usage
- **OpenSpec candidate**: `add-resolution-poller`

### ~~BL-02: Switch LLM Provider to Gemini Flash 2.0~~ ✅ DONE
`GeminiFlashSignalProvider` added to `llm_adapter.py`. `LLM_PROVIDER=gemini` (default) uses Gemini Flash free tier.
`LLM_PROVIDER=anthropic` selects Claude Haiku. Both providers share the same interface.
- **Note on MiniMax**: Skipped — primarily optimized for Chinese-language tasks.

### ~~BL-03: Parser Crash Alerting in n8n~~ ✅ DONE
Parser Signal node has `onError: continueErrorOutput`. Error path → "Parser Down" Telegram node.
Fires a ⚠️ alert with error message when the parser is unreachable.

### ~~BL-09: LLM Event Classification via refine()~~ ✅ DONE
Implement `GeminiFlashSignalProvider.refine()` to correct event_type and assets using the LLM when the heuristic is low-confidence.
Currently the refine path triggers (confidence < 0.65 or UNKNOWN/MISC_OTHER) but the method is a stub returning empty.
- **Why**: Wrong event_type is cosmetic but misleading in alerts; correct labels improve signal interpretability
- **Effort**: Low — implement the refine prompt in GeminiFlashSignalProvider; pattern already exists in the codebase
- **Note**: Does NOT affect p_model, edge, or GO/NO_GO verdict

## 🟡 Medium Priority

### ~~BL-04: Richer News Sources~~ ✅ DONE
Tavily bumped to 5 results. CryptoPanic RSS fetched inline in Build Signal Input (no API key needed), filtered by market question keywords. Text budget increased from 3000 → 5000 chars. Workflow v3 released: `Predict Market Risk Scanner v3.json`.

### ~~BL-05: Fix Deployed Capital Tracking~~ ✅ DONE
Added server-side atomic capital ledger in SQLite. `RiskRequest.auto_reserve=true` makes `/risk` atomically read current deployed capital and reserve `bet_size` on GO — no more static n8n variable race.
New endpoints: `GET /deployed`, `POST /deployed/release`, `POST /deployed/reset`.
- **n8n change**: set `auto_reserve: true` in Risk node body; remove static DEPLOYED variable.

### ~~BL-06: Confidence-Weighted Kelly Fraction~~ ✅ DONE
Scale the Kelly fraction by parser `confidence` (e.g., `kelly_fraction = base * confidence`).
Low-confidence signals currently bet at the same fraction as high-confidence ones.
- **Why**: Better position sizing — bet less when the model is uncertain
- **Effort**: Low — one-line change in risk.py or n8n Risk call params

## 🔴 High Priority (Short-Term Focus)

### ~~BL-10: Resolution Date Filter (≤10 Days)~~ ✅ DONE
Filter out markets that resolve more than N days from now (default: 10 days). Extract `end_date` from Polymarket API response and filter in "Parse & Filter Markets" node.
- `MAX_DAYS_TO_EXPIRY = 10` in n8n. `days_to_expiry` flows through the entire pipeline.
- Workflow v4 released: `Predict Market Risk Scanner v4.json`.

### ~~BL-11: Time-to-Expiry Kelly Scaling~~ ✅ DONE
Scale bet size by time urgency: day 1 = 1.0×, day 10 = 0.5× (linear, clamped). `days_to_expiry` sent to `/risk`, applied in `risk.py` as `time_multiplier`. Displayed in Telegram alert as `expires: ⏳ N days`.
- `RiskRequest.days_to_expiry: int | None` added to model.
- 6 new tests in `test_risk.py` covering monotonicity, day1=1.0×, day10=0.5×, clamp, and API.

### ~~BL-12: Market Momentum Filter~~ ✅ DONE
Track whether `p_market` is moving toward or away from our thesis direction.
- "Momentum Tracker" Code node added between Parse & Filter Markets and Tavily Search.
- Reads/writes `pm_history` from n8n static workflow data (48h TTL).
- Adds `momentum_delta` and `momentum_dir` (rising/falling/flat/unknown) to each market.
- Displayed in Telegram alerts: `sentiment: ... | momentum: 📈 +2.3%` (or 📉/➡️/❓).
- Display-only (no hard filter) — user can see momentum and decide manually for now.

## 🟡 Medium Priority

### ~~BL-13: Market Category / Tag Filter~~ ✅ DONE
`MARKET_TAG` n8n variable added. Defaults to `crypto`. Passed as query param to Polymarket API in Fetch Markets node. Workflow v4 released.

## 🔴 High Priority (Wallet-Flow Phases)

### BL-14: Wallet Track-Record Edge Scoring
Score veteran wallets by realized edge over resolved markets (`edge = outcome − avg_entry_price`, stake-weighted), with empirical-Bayes shrinkage (`n/(n+k)` toward the population mean, k≈20) and a period-split persistence test to separate skill from luck. Live signal: which side proven-edge wallets are entering.
- **Why**: The Phase 1 detector only catches *new* wallets (insider proxy); skilled veterans are explicitly skipped today (`fetch_wallet_metadata` bails at ≥2 markets)
- **Effort**: High — needs the incremental trade ledger (BL-15) as substrate
- **OpenSpec candidate**: `add-wallet-track-record`

### BL-15: Maker Fills + Incremental Trade Ledger
Store trades in SQLite incrementally (dedupe by transaction hash, fetch only newer than last stored), include maker fills (`takerOnly=false`). Removes the ~3000-trade snapshot cap and the passive-accumulation blind spot — informed traders working limit orders are invisible to the current taker-only snapshot.
- **Effort**: Medium
- **OpenSpec candidate**: `add-trade-ledger`

### BL-16: Funding-Source Wallet Clustering
Resolve each proxy wallet's USDC funding source on Polygon (Polygonscan API, cached). Wallets funded by the same address are one actor; a new wallet whose funder also funded previously flagged winners is a much stronger flag than wallet age alone (age is gameable).
- **Effort**: Medium — one API call per wallet, cache forever
- **OpenSpec candidate**: `add-funding-clusters`

### BL-17: Post-Entry Price Drift Metric
For flagged flow, track market price movement since the flagged wallets' average entry. Confirms informed flow continuously (did the market drift toward the dominant side?) and doubles as a live confirmation signal. Needs repeated scans of the same market (already supported by `flow_scans` history).
- **Effort**: Low–Medium once BL-15 lands
- **OpenSpec candidate**: `add-flow-drift`

## 🟢 Lower Priority

### BL-18: Strategies Layout + Package Rename
Repo renamed to `polymarket-engine` (June 2026). Finish the identity change structurally: move signal sources under a `strategies/` package (news_signal, wallet_flow, future hk_temp), rename the `crypto_news_parser` package to match, keep the platform core (risk, ledger, storage, calibration) at top level.
- **Why**: the repo is a multi-strategy trading platform; the package name and flat layout still say "news parser"
- **Effort**: Medium — mechanical moves + import updates; do it alongside an approved code change, not as a standalone churn PR
- **OpenSpec candidate**: `refactor-strategies-layout`

### BL-07: Multi-Outcome Market Support
Polymarket has markets with 3+ outcomes (e.g., "Which chain will have highest TVL?").
Currently the pipeline only handles binary YES/NO markets (first outcome price only).
- **Why**: Opens up a much larger market universe
- **Effort**: High — needs new price extraction logic, different Kelly formula for multi-outcome

### ~~BL-08: Prompt Versioning for Signal LLM~~ ✅ DONE
`signal_prompt_version` added to `SignalResponse`. Set to `SIGNAL_PROMPT_VERSION` when LLM path is used, `null` for heuristic.
Needed for A/B testing prompt changes against calibration outcomes.
