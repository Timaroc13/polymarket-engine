# Change: Per-strategy capital tagging for a shared multi-bot bankroll

## Why

Two strategies now bet from the same bankroll: the crypto news/signal pipeline (sizes via `POST /risk` + `auto_reserve`) and the HK temperature bot (`hk-weather-bot` repo, sizes locally in `05_kelly.py` with no view of shared exposure). The `/risk` Max Exposure rule (deployed + bet ≤ 30% of bankroll) is only meaningful if **all** strategies reserve from one ledger — today the HK bot's positions are invisible to it, so the cap can be silently breached.

This change makes the existing capital ledger multi-strategy aware and is Step 1 of consolidating the trading bots onto the parser's risk engine (Step 2 — moving HK bot code into this repo as a strategy module — is a later decision).

## What Changes

- **`capital-ledger` capability (ADDED, documents existing + new behaviour)**:
  - `RiskRequest` gains optional `strategy: str` (e.g. `"news-signal"`, `"hk-temp"`); stored on reservations.
  - `GET /deployed` returns the total plus a per-strategy breakdown.
  - `POST /deployed/release` accepts an optional `bet_id` so a specific reservation can be released exactly once (today release is amount-only and unlinked).
- **HK bot integration (external, tracked in tasks)**: `05_kelly.py` calls `POST /risk` with `strategy="hk-temp"`, `auto_reserve=true`, and a `bet_id`; settled bets call `/deployed/release` with that `bet_id`. Requires the parser service reachable from the bot (local uvicorn or Cloud Run URL in its config).

**Non-breaking**: `strategy` is optional (defaults to `"default"`); existing callers see no change.

## Impact

- Affected specs: `capital-ledger` (new capability spec; documents reservation semantics that previously lived only in code)
- Affected code: `models.py` (RiskRequest.strategy, DeployedResponse breakdown), `storage.py` (reservations.strategy column + breakdown query + release-by-bet_id), `main.py` (/risk, /deployed, /deployed/release), tests
- External: `hk-weather-bot` repo — replace local Kelly sizing decision with `/risk` call (its model still produces p_model; only sizing/verdict moves)
- **Non-goals**: moving HK bot code into this repo; auto-releasing reservations on market resolution (manual/explicit for now)
