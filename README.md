# crypto-news-parser

API-first service that converts unstructured crypto-related text into a single canonical event object, plus a Polymarket trading-signal pipeline (`/signal`, `/risk`, `/flow-scan`).

See [METHODOLOGY.md](METHODOLOGY.md) for the wallet-flow signal methodology, validation gates, and operational runbook.

## Quickstart (local)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# Optional: copy env template
Copy-Item .env.example .env

# Run API
uvicorn crypto_news_parser.main:app --app-dir src --reload --port 8000 --env-file .env
```

Test:

```powershell
pytest
```

## API

- `POST /parse`
  - Body: `{ "text": "...", "deterministic": false, "source_url": "https://..." }` (source URL is accepted as metadata only; it is not fetched)
  - Returns a schema-valid response (v2 taxonomy).
    - Uses `event_type="UNKNOWN"` for non-crypto or unclassifiable inputs.
    - Uses `event_type="MISC_OTHER"` for crypto-related inputs that don't map to a specific v2 category.
  - Response may include:
    - `event_subtype` (optional, implementation-defined, consistent with `event_type`).
      - Examples: `stablecoin.launch.registered`, `regulation.enforcement.lawsuit`, `protocol.upgrade.hard_fork`
    - `v1_event_type` (optional legacy mapping) for migration/debugging.

- `POST /parse_url`
  - Body: `{ "url": "https://...", "deterministic": false }`
  - Fetches the URL with SSRF protections and extracts readable text before parsing.

- `POST /signal`
  - Body: `{ "text": "...", "market_question": "Will BTC exceed $100k?", "deterministic": false }`
  - Returns a market signal for use in prediction market risk pipelines.
  - Response fields:
    - `p_model` (float, 0.0–1.0): directional probability signal.
    - `p_model_method` (string): `"llm"` when Claude Haiku estimated the probability, `"heuristic"` otherwise.
    - `market_direction` (bullish / bearish / neutral): derived from `p_model` thresholds (>0.55 bullish, <0.45 bearish).
    - Supporting parse signals: `event_type`, `sentiment`, `impact_score`, `confidence`, `assets`, `jurisdiction`.
  - **LLM path** (enabled with `LLM_ENABLE=1`):
    - When `market_question` is provided, an LLM estimates the true probability that the question resolves YES.
    - Falls back to the heuristic automatically on any failure or when `market_question` is absent.
    - Provider is selected via `LLM_PROVIDER` env var (default: `gemini`).
  - **Heuristic formula** (deterministic, no LLM required):
    ```
    sentiment_adj  = +0.25 (positive) | -0.25 (negative) | 0.0 (neutral)
    impact_weight  = 0.5 + impact_score * 0.5
    p_model        = clamp(0.5 + sentiment_adj * impact_weight * confidence, 0.05, 0.95)
    ```

- `POST /risk`
  - Body: `{ "p_model": 0.65, "p_market": 0.50, "bankroll": 5000, "deployed": 800 }`
  - Runs 5 hard rules against Kelly/VaR math and returns a GO or NO_GO verdict.
  - Response: `{ "verdict": "GO", "bet_size": 180.00, "ev": 54.00, "kelly_f": 0.30, "edge": 0.15, "rules": [...] }`
  - All 5 rules must pass for GO:
    1. **Minimum Edge** — `p_model - p_market ≥ min_edge` (default 4%)
    2. **Kelly Positive** — Kelly f* > 0 (positive EV)
    3. **Max Single Bet** — bet ≤ `max_bet_fraction × bankroll` (default 5%)
    4. **Max Exposure** — `deployed + bet ≤ max_exposure_fraction × bankroll` (default 30%)
    5. **VaR 95%** — 95th-pct loss ≤ `var_tolerance × bankroll` (default 10%)
  - Optional config overrides: `min_edge`, `kelly_fraction`, `max_bet_fraction`, `max_exposure_fraction`, `var_tolerance`

- `POST /flow-scan`
  - Body (all optional): `{ "top_n": 20, "max_days": 7, "min_liquidity": 10000, "max_wallets": 300 }` or `{ "condition_id": "0x..." }` for a single market.
  - `max_wallets` caps wallet-metadata lookups per market to the top-N wallets by position size (the slow part). Unset = full fidelity, but a single high-volume market can take 15+ minutes; `300` is a good scheduled-scan default.
  - Scans active Polymarket markets for informed-trading flow: reconstructs per-wallet net positions from recent trades and flags **new wallets** (≤14 days old, exactly 1 market traded) piling onto one side.
  - Returns per market: `signal_score` (0–100), `risk_tier` (LOW/MEDIUM/HIGH), `dominant_side`, new-wallet counts/USDC, `recent_burst_pct`, and `p_market_at_scan` (YES implied probability at scan time).
  - When persistence is enabled, every scanned market is logged to `flow_scans` and auto-registered in `tracked_markets`, so the existing `POST /poll-resolutions` cron resolves it.
  - **Slow by design**: a 20-market scan makes hundreds of Polymarket API calls and can take minutes. Call it from a scheduler (e.g. an n8n cron every few hours), not interactively.

- `GET /flow-calibration`
  - Requires persistence. Joins the latest flow scan per market with resolved outcomes and reports, per risk tier and overall:
    `n`, `wins` (dominant side matched the outcome), `win_rate`, `avg_implied` (dominant-side implied probability at scan), and `lift = win_rate - avg_implied`.
  - This is the validation harness for the flow signal: a positive `lift` on a meaningful sample (n ≥ 30+) is the evidence that the detector has edge; until then treat tiers as unvalidated.

- `POST /feedback`
  - Body: `{ "parse_id": 123, "expected": { ... }, "notes": "..." }`
  - Or (when you don't have `parse_id`): `{ "input_id": "...", "text": "...", "expected": { ... } }`
  - Requires persistence enabled.

## Environment

- `API_KEY` (optional): if set, requires `Authorization: Bearer <API_KEY>`
- `MODEL_VERSION` (optional): included in responses
- `ENABLE_PERSISTENCE` (optional): set to `1` to store parse runs and accept feedback
- `DB_PATH` (optional): path to SQLite DB file (default: `./data.sqlite3`)

## Feedback export

When persistence is enabled and feedback has been collected, export eval-compatible JSONL:

```powershell
C:/dev/crypto-news-parser/.venv/Scripts/python.exe scripts/export_feedback.py --out eval/feedback_cases.jsonl
```

## Deploy (Google Cloud Run)

Prereqs:

- Google Cloud CLI (`gcloud`) installed: https://cloud.google.com/sdk/docs/install
- Authenticate + initialize once:
  - `gcloud init`
  - `gcloud auth login`
- A GCP project selected: `gcloud config set project <PROJECT_ID>`
- Artifact Registry enabled (recommended)

Recommended env vars:

- `MODEL_VERSION` (set to a release tag / commit SHA for traceability)
- `API_KEY` (optional; if set, requires `Authorization: Bearer <API_KEY>`)
- `LLM_ENABLE=1` — enables LLM-powered `p_model` estimation in `POST /signal`
- `LLM_PROVIDER` — which provider to use: `gemini` (default, free tier) | `anthropic`
- `GEMINI_API_KEY` — required when `LLM_PROVIDER=gemini` (Google AI Studio free tier: 1M tokens/day)
- `ANTHROPIC_API_KEY` — required when `LLM_PROVIDER=anthropic` (Claude Haiku)

Build and deploy from this repo root:

```powershell
# Authenticate once
gcloud auth login

# Recommended: use Artifact Registry
# One-liner deploy helper:
./scripts/deploy_cloud_run.ps1 -ProjectId <PROJECT_ID> -Region us-central1 -AllowUnauthenticated
```

Notes:

- The container reads `$env:PORT` on Cloud Run (defaults to 8080).
- For private services, omit `-AllowUnauthenticated`.
- For API keys, prefer Secret Manager and bind via `--set-secrets`.

## Golden evals

- Add 10–30 examples to [eval/golden_cases.jsonl](eval/golden_cases.jsonl)
- Run: `C:/dev/crypto-news-parser/.venv/Scripts/python.exe scripts/run_eval.py`
