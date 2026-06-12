# Tasks: add-strategy-capital-tags

> Blocked on proposal approval.

## 1. Parser service

- [ ] 1.1 `capital_reservations.strategy` column (default 'default'); migration-safe ALTER on init
- [ ] 1.2 `RiskRequest.strategy` field; pass through `/risk` auto_reserve path
- [ ] 1.3 `GET /deployed`: per-strategy breakdown from active reservations
- [ ] 1.4 `POST /deployed/release`: optional `bet_id` → release that reservation idempotently (delete row + subtract amount)
- [ ] 1.5 Tests: tagged reservation, default tag, breakdown math, release-by-bet_id idempotency

## 2. HK bot integration (hk-weather-bot repo)

- [ ] 2.1 Add parser URL + API key to `config.py`; `strategy="hk-temp"`
- [ ] 2.2 `05_kelly.py`: replace local sizing verdict with `POST /risk` (`auto_reserve=true`, `bet_id` = market slug + date); keep local p_model computation
- [ ] 2.3 `log_bet.py` settlement path: call `/deployed/release` with the bet's `bet_id`
- [ ] 2.4 Decide where the parser runs for the bot (local uvicorn service vs Cloud Run) and document in the bot README

## 3. Validation

- [ ] 3.1 `ruff check`, full pytest, `openspec validate add-strategy-capital-tags --strict --no-interactive`
