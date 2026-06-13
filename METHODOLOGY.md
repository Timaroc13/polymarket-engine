# Wallet-Flow Signal — Methodology & Runbook

How we identify Polymarket traders with edge, validate the signal before trusting it, and
what to run day-to-day. This documents the `wallet-flow` capability (OpenSpec change
`add-wallet-flow-signal`), the in-app scheduler and dashboard that operate it, and the
phases that follow.

---

## 1. The idea

Two distinct trader populations carry information:

| Population | Tell | Phase |
|---|---|---|
| **Insiders** | Fresh wallets (≤14 days old, first market ever) deploying real money on one side of a near-resolution market | **Phase 1 (live now)** |
| **Skilled veterans** | Wallets with a multi-market track record of buying at prices below resolution value | Phase 3 (BL-14) |

Phase 1 ports the standalone detector (`poly-wallets` repo) into the engine and —
critically — wires it to ground truth. Every scan is logged with the market's implied
probability at scan time; every scanned market is tracked until resolution; the dashboard
then tells us whether the signal is real.

**Nothing bets on this signal until the data says it works.** Phase 1 is observe-and-validate only.

## 2. The pipeline

Everything below runs inside one process (the engine), started automatically at logon.
No n8n, no external scheduler.

```
in-app scheduler (every 4h)      in-app scheduler (every 15m)     you (anytime)
      │                                │                               │
  do_flow_scan ──► flow_scans     do_poll_resolutions          GET /dashboard
      │            (history)           │                               │
      ├──► tracked_markets ◄────────── resolves outcomes ───────► lift + paper PnL
      │                                                               charts
      └──► Telegram alert
           (HIGH tier)
```

1. **Scan** (`do_flow_scan`, every `SCAN_INTERVAL_HOURS`): pulls top-volume markets resolving
   within 7 days, reconstructs per-wallet net USDC positions from recent trades, flags new
   wallets, scores each market 0–100 → LOW / MEDIUM / HIGH, records `p_market_at_scan` (YES
   implied probability), stores the row, and **sends a Telegram alert for each HIGH-tier market**.
2. **Resolve** (`do_poll_resolutions`, every `POLL_INTERVAL_MINUTES`): scanned markets are
   auto-registered in `tracked_markets`; the poller marks them resolved with the outcome.
3. **Evaluate** (`GET /dashboard`, anytime): joins the **latest scan per market before
   resolution** with the outcome and shows two complementary lenses:
   - **Calibration / lift** (probability lens) — `lift = win_rate − avg_implied`, per tier
   - **Paper trading** (money lens) — flat virtual stake on the dominant side at scan-time
     price; equity curve, PnL, ROI, max drawdown

   Raw JSON is still at `GET /flow-calibration` and `GET /dashboard/data`.

### Two lenses, why both

`lift` measures whether the flagged side wins *more often than priced*. Paper PnL measures
whether it makes *money* — and they can disagree (a signal that always backs 90¢ favorites
can have positive lift but negative PnL once a longshot busts it). The gates below are
defined on **lift**; paper PnL is the sanity check that the edge survives contact with prices.

## 3. Decision gates (pre-committed, so we don't fool ourselves)

| Gate | Threshold | Action |
|---|---|---|
| **G1 — Signal exists** | HIGH tier: `n ≥ 30` and `lift ≥ +0.05` | Proceed to G2; otherwise retune scoring or kill |
| **G2 — Survives costs** | `lift` ≥ fees + spread (~3–5% on Polymarket) **and** paper PnL positive over the same window | Wire tier into `/risk` as a **veto/boost**, still no auto-betting |
| **G3 — Persists** | Lift holds on a fresh out-of-sample window (next ~30 resolutions) | Allow flow signal to size real bets via the existing Kelly/VaR rules |

Rules we hold ourselves to:

- **No peeking-and-tuning on the same data.** If we change scoring weights, the calibration
  counter conceptually resets — only post-change scans count toward gates (filter by scan date).
- **Small-n humility**: with n=30, a true 50% win rate produces ±18% swings; treat lift < ±0.10
  at n<30 as noise. Paper PnL is *noisier* than lift at small n — don't let a lucky longshot
  on the equity curve override the gate.
- **MEDIUM/LOW tiers are the control group.** If LOW shows the same lift (or paper ROI) as
  HIGH, the score isn't discriminating — the tiers are decoration.
- The news-LLM `p_model` signal is validated the same way via `/feedback` + resolutions
  (Brier/calibration), independently. The two signals only get combined after both pass G1.

## 4. Phase roadmap

| Phase | Backlog | What it adds | Unblocks |
|---|---|---|---|
| 1 (now) | — | Detector + scan logging + calibration + paper sim, scheduler, dashboard | The go/no-go data |
| 2 | BL-15 | Incremental trade ledger + maker fills (removes 3000-trade cap and the passive-accumulation blind spot) | BL-14, BL-17 |
| 3 | BL-14 | Veteran wallet track-record scoring: realized edge per wallet, empirical-Bayes shrinkage (`n/(n+k)`, k≈20), period-split persistence test | "Smart money" signal + Kelly-sized paper bets |
| 4 | BL-16 | Funding-source clustering (Polygon USDC funders) — catches aged/split insider wallets | Stronger flags |
| 5 | BL-17 | Post-entry drift: does price move toward the flagged side after the scan? | Live confirmation signal |

Each phase is its own OpenSpec change (proposal → approval → apply → archive).

## 5. How to run it

### It's already running

The server is registered as the **`PolymarketEngine_Server`** scheduled task (hidden, starts
at logon, auto-restarts with the machine). Config lives in `.env` — currently:
`ENABLE_PERSISTENCE=1`, `SCHEDULER_ENABLE=1`, Telegram token + chat id set. So in normal
operation there is **nothing to start**: scans, resolution polling, and HIGH-tier Telegram
alerts happen on their own.

Scheduler tuning (all optional, in `.env`): `SCAN_INTERVAL_HOURS` (4), `POLL_INTERVAL_MINUTES`
(15), `SCAN_TOP_N` (20), `SCAN_MAX_WALLETS` (300), `ALERT_MIN_TIER` (HIGH — set MEDIUM for more
alerts). Paper sim: `PAPER_STAKE` (100), `PAPER_FEE` (0.02).

### Daily / weekly: just look

- **Phone buzzes** → a HIGH-tier signal. Read side, new-wallet count/$, YES price at scan,
  days to expiry. These are watch-and-learn during validation — no action.
- **Weekly check** → open **http://localhost:8000/dashboard**. Read two numbers in the
  **HIGH** column: `n` (need 30) and `lift` (need ≥ +0.05). Glance at the paper equity curve
  for the money lens. Everything auto-refreshes; LOW/MEDIUM are your control groups.

### Manual controls (rarely needed)

```powershell
# Restart / stop the server task
Start-ScheduledTask -TaskName "PolymarketEngine_Server"
Stop-ScheduledTask  -TaskName "PolymarketEngine_Server"

# Force a one-off scan (PowerShell: use curl.exe, not the curl alias)
curl.exe -X POST http://localhost:8000/flow-scan -H "Content-Type: application/json" -d '{\"top_n\": 5}'

# Scan one specific market
curl.exe -X POST http://localhost:8000/flow-scan -H "Content-Type: application/json" -d '{\"condition_id\": \"0x...\"}'

# Raw calibration JSON (behind the dashboard)
curl.exe http://localhost:8000/flow-calibration
```

A full default scan (top 20) makes hundreds of paced Polymarket API calls and takes minutes —
that's why it runs on a 4-hour timer, not on demand.

### Run from source manually (dev / first-time)

```powershell
cd C:\dev\crypto-news-parser
.\.venv\Scripts\Activate.ps1
uvicorn crypto_news_parser.main:app --app-dir src --port 8000 --env-file .env
```

### Tests & evals

```powershell
pytest -q                    # full suite (wallet_flow, scheduler, dashboard, paper, ...)
ruff check .                 # lint (CI runs this; CI is green on main)
openspec validate --strict --no-interactive
```

## 6. Known Phase-1 limitations (accepted, tracked)

- **Taker-only trades**: informed traders working passive limit orders are invisible (BL-15).
- **~3000-trade window**: high-volume markets only show recent flow; early quiet accumulation
  is missed (BL-15).
- **Wallet age is gameable**: pre-aged or split wallets evade the "new wallet" filter (BL-16).
- **Binary markets only**: multi-outcome markets use first-outcome price only (BL-07).
- **Flat-stake paper sim**: no Kelly sizing — the flow signal has a side+tier, not a
  probability; honest sizing waits for the BL-14 track-record model.
- **Scan latency**: minutes per scan — fine for a 4-hour timer, wrong for real-time reaction.
- **Local-only**: runs while your PC is on. A missed scan just slows data accumulation;
  nothing is lost. Move to a $5 VPS (same one process) when real money rides on it — note
  the SQLite DB must travel with it, which rules out ephemeral hosts like Cloud Run.
