# Change: Add market-signal endpoint for prediction market probability estimation

## Why

The predict-market-risk pipeline needs a probability estimate (`p_model`) from news to feed into Kelly-based risk validation. The current `POST /parse` returns event classification, sentiment, impact_score, and confidence — but does not expose a ready-to-use probability float. Consumers must manually translate these fields into a signal, producing inconsistent and brittle integrations.

This change adds a dedicated `POST /signal` endpoint that accepts news text plus a prediction market question and returns a `p_model` float (0.0–1.0) and a `market_direction` label, derived from the existing parse pipeline.

## What Changes

- Add a new `POST /signal` endpoint accepting `text` + `market_question`
- Return `p_model` (float, 0.0–1.0) — a directional probability signal derived heuristically from sentiment, impact_score, and confidence
- Return `market_direction` (bullish / bearish / neutral) — derived from the same signals
- Return supporting parse signals (`event_type`, `sentiment`, `impact_score`, `confidence`, `assets`, `jurisdiction`) for traceability
- Reuse the existing parse pipeline; no new NLP or LLM dependencies in v1

## Impact

- Affected specs: `market-signal` (new capability), `parse-api` (no change — signal is a separate endpoint)
- Affected code: new `SignalRequest` / `SignalResponse` models, new route in `main.py`, new `compute_market_signal()` function in `parser.py`
- **Non-goals**: question-specific semantic reasoning (v1 uses a formula, not LLM interpretation of the market question), Polymarket API integration, n8n workflow, validate_risk.py script, Telegram bot
- **Non-breaking**: `POST /parse` is unchanged
