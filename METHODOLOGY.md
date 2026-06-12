# Wallet-Flow Signal — Methodology & Runbook

How we identify Polymarket traders with edge, validate the signal before trusting it, and
what to run day-to-day. This documents the plan behind the `wallet-flow` capability
(OpenSpec change `add-wallet-flow-signal`) and the phases that follow.

---

## 1. The idea

Two distinct trader populations carry information:

| Population | Tell | Phase |
|---|---|---|
| **Insiders** | Fresh wallets (≤14 days old, first market ever) deploying real money on one side of a near-resolution market | **Phase 1 (live now)** |
| **Skilled veterans** | Wallets with a multi-market track record of buying at prices below resolution value | Phase 2 (BL-14) |

Phase 1 ports the standalone detector (`poly-wallets` repo) into the parser service and —
critically — wires it to ground truth. Every scan is logged with the market's implied
probability at scan time; every scanned market is tracked until resolution; a calibration
report tells us whether the signal is real.

**Nothing bets on this signal until the data says it works.** Phase 1 is observe-and-validate only.

## 2. The pipeline

```
n8n cron (every 4h)            n8n cron (every 15m)         weekly (manual)
      │                              │                            │
POST /flow-scan ──► flow_scans  POST /poll-resolutions      GET /flow-calibration
      │             (history)        │                            │
      └──► tracked_markets ◄─────── resolves outcomes ──────► win_rate vs implied
```

1. **Scan**: `POST /flow-scan` pulls top-volume markets resolving within 7 days, reconstructs
   per-wallet net USDC positions from recent trades, flags new wallets, scores each market
   0–100 → LOW / MEDIUM / HIGH tier, and records `p_market_at_scan` (YES implied probability).
2. **Resolve**: scanned markets are auto-registered in `tracked_markets`; the existing
   `POST /poll-resolutions` cron marks them resolved with the outcome.
3. **Calibrate**: `GET /flow-calibration` joins the **latest scan per market before resolution**
   with the outcome and reports, per tier and overall:
   - `n` — resolved markets with a dominant side
   - `win_rate` — how often the dominant (new-wallet) side won
   - `avg_implied` — what the market priced that side at, at scan time
   - `lift` = `win_rate − avg_implied` — **the edge estimate**

## 3. Decision gates (pre-committed, so we don't fool ourselves)

| Gate | Threshold | Action |
|---|---|---|
| **G1 — Signal exists** | HIGH tier: `n ≥ 30` and `lift ≥ +0.05` | Proceed to G2; otherwise retune scoring or kill |
| **G2 — Survives costs** | `lift` ≥ fees + spread (~3–5% on Polymarket) | Wire tier into `/risk` as a **veto/boost**, still no auto-betting |
| **G3 — Persists** | Lift holds on a fresh out-of-sample window (next ~30 resolutions) | Allow flow signal to size real bets via the existing Kelly/VaR rules |

Rules we hold ourselves to:

- **No peeking-and-tuning on the same data.** If we change scoring weights, the calibration
  counter conceptually resets — only post-change scans count toward gates (filter by scan date).
- **Small-n humility**: with n=30, a true 50% win rate produces ±18% swings; treat lift < ±0.10
  at n<30 as noise.
- **MEDIUM/LOW tiers are the control group.** If LOW shows the same lift as HIGH, the score
  isn't discriminating — the tiers are decoration.
- The news-LLM `p_model` signal is validated the same way via `/feedback` + resolutions
  (Brier/calibration), independently. The two signals only get combined after both pass G1.

## 4. Phase roadmap

| Phase | Backlog | What it adds | Unblocks |
|---|---|---|---|
| 1 (now) | — | Detector + scan logging + calibration | The go/no-go data |
| 2 | BL-15 | Incremental trade ledger + maker fills (removes 3000-trade cap and the passive-accumulation blind spot) | BL-14, BL-17 |
| 3 | BL-14 | Veteran wallet track-record scoring: realized edge per wallet, empirical-Bayes shrinkage (`n/(n+k)`, k≈20), period-split persistence test | "Smart money" signal |
| 4 | BL-16 | Funding-source clustering (Polygon USDC funders) — catches aged/split insider wallets | Stronger flags |
| 5 | BL-17 | Post-entry drift: does price move toward the flagged side after the scan? | Live confirmation signal |

Each phase is its own OpenSpec change (proposal → approval → apply → archive).

## 5. How to run it

### One-time setup (local)

```powershell
cd C:\dev\crypto-news-parser
.\.venv\Scripts\Activate.ps1

# .env (or env vars):
#   ENABLE_PERSISTENCE=1        ← required: scans/resolutions must be stored
#   DB_PATH=C:\dev\crypto-news-parser\data.sqlite3
#   API_KEY=<optional bearer key>

uvicorn crypto_news_parser.main:app --app-dir src --port 8000 --env-file .env
```

### Manual scan (first run / spot checks)

```powershell
# Default: top 20 markets, ≤7 days to resolution, ≥$10k liquidity. Takes minutes — be patient.
curl -X POST http://localhost:8000/flow-scan -H "Content-Type: application/json" -d '{}'

# Smaller/faster smoke test:
curl -X POST http://localhost:8000/flow-scan -H "Content-Type: application/json" -d '{"top_n": 5}'

# One market you're suspicious about:
curl -X POST http://localhost:8000/flow-scan -H "Content-Type: application/json" -d '{"condition_id": "0x..."}'
```

### Scheduled operation (n8n)

1. **Flow Scan** workflow: import [n8n/flow-scan-workflow.json](n8n/flow-scan-workflow.json) —
   4-hour cron → `POST /flow-scan` (10-min timeout) → Telegram alert for every `HIGH`-tier market,
   plus a "Scanner Down" alert on errors. Set n8n variables: `PARSER_URL`, `PARSER_API_KEY`
   (optional), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
2. **Poll Resolutions**: already exists in the predict-market-risk workflow (15-minute cron
   hitting `/poll-resolutions`) — flow-scanned markets ride the same poller automatically.

### Weekly review

```powershell
curl http://localhost:8000/flow-calibration
```

Read it against the gates in §3. Expect `n` to grow by roughly the number of scanned markets
that resolve per week (with 7-day-expiry markets, most of what you scan resolves within the week).

### Tests & evals

```powershell
pytest -q                    # full suite, includes tests/test_wallet_flow.py
ruff check .                 # lint (CI runs this)
openspec validate add-wallet-flow-signal --strict --no-interactive
```

## 6. Known Phase-1 limitations (accepted, tracked)

- **Taker-only trades**: informed traders working passive limit orders are invisible (BL-15).
- **~3000-trade window**: high-volume markets only show recent flow; early quiet accumulation
  is missed (BL-15).
- **Wallet age is gameable**: pre-aged or split wallets evade the "new wallet" filter (BL-16).
- **Binary markets only**: multi-outcome markets use first-outcome price only (BL-07).
- **Scan latency**: minutes per scan — fine for a 4-hour cron, wrong for real-time reaction.
