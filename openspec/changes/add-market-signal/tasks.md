# Tasks: add-market-signal

## 1. Models

- [x] 1.1 Add `SignalRequest` Pydantic model to `models.py`
  - Fields: `text` (str, required, reuse MAX_TEXT_LENGTH validator), `market_question` (str, optional), `deterministic` (bool, default False), `input_id` (str | None)
- [x] 1.2 Add `SignalResponse` Pydantic model to `models.py`
  - Fields: `p_model` (float, ge=0.0, le=1.0), `market_direction` (MarketDirection), `event_type` (EventType), `sentiment` (Sentiment), `impact_score` (float), `confidence` (float), `assets` (list[str]), `jurisdiction` (Jurisdiction), `schema_version` (str), `model_version` (str)

## 2. Parser

- [x] 2.1 Add `compute_market_signal(sentiment, impact_score, confidence) -> tuple[float, MarketDirection]` to `parser.py`
  - Implement the formula from `design.md`
  - Return `(p_model, market_direction)` where direction is derived from `p_model` thresholds

## 3. Route

- [x] 3.1 Add `POST /signal` route to `main.py`
  - Enforce max text length (reuse existing 413 pattern)
  - Call `select_primary_event`, `extract_assets`, `resolve_jurisdiction`, `infer_sentiment`
  - Call `compute_market_signal` with the results
  - Optionally call `_maybe_refine` if LLM provider is available (same low-confidence threshold as `/parse`)
  - Return `SignalResponse`
- [x] 3.2 Add `/signal` to the content-type middleware allow-list (alongside `/parse` and `/parse_url`)

## 4. Tests

- [x] 4.1 Unit tests for `compute_market_signal()` in `tests/test_signal.py`
  - Positive sentiment + high impact → p_model > 0.55
  - Negative sentiment + high impact → p_model < 0.45
  - Neutral sentiment → p_model near 0.5 (within ±0.06)
  - Zero confidence → p_model == 0.5
- [x] 4.2 Integration tests for `POST /signal` in `tests/test_signal.py`
  - Valid request returns HTTP 200 with schema-valid response
  - `p_model` always in [0.0, 1.0]
  - `market_direction` always present (not null)
  - Oversized text returns HTTP 413
  - Missing text returns HTTP 422
  - Non-JSON Content-Type returns HTTP 415
- [x] 4.3 Deterministic mode test: same `text` + `deterministic=true` → same `p_model` on repeated calls

## 5. Docs

- [x] 5.1 Update `README.md` to document `POST /signal` endpoint (request/response shape, `p_model` formula, v1 limitations)

## 6. Validation

- [x] 6.1 Run `openspec validate add-market-signal --strict --no-interactive` and resolve all issues
