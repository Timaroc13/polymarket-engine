# Design: Market Signal Endpoint

## Context

The predict-market-risk pipeline (documented in `predict-market-risk-rundown.md`) connects:
1. A news parser (this project) — generates `p_model`
2. A risk validator (`validate_risk.py`) — applies Kelly/VaR rules using `p_model` vs `p_market`
3. An orchestrator (n8n) — fetches Polymarket markets, calls the parser and validator, sends Telegram alerts

The news parser must output a float `p_model` that can flow into the risk validator without manual translation. The key design question is: how do we derive a meaningful probability from parsed signals, and what role does the `market_question` play in v1?

## Goals / Non-Goals

- **Goals**
  - Expose `p_model` as a first-class output from a dedicated endpoint
  - Keep v1 deterministic and dependency-free (pure heuristic, no LLM call required)
  - Make `market_question` an accepted field (for future use) without acting on it in v1
  - Integrate naturally with the existing parse pipeline
- **Non-Goals**
  - Semantic interpretation of `market_question` in v1 (LLM-based question answering is v2)
  - Polymarket or Kalshi API integration (lives in n8n, not here)
  - validate_risk.py or Kelly logic (separate script/service)
  - Changing `POST /parse` response shape

## Decisions

### Decision: Separate endpoint (`POST /signal`) rather than adding `p_model` to `POST /parse`

**Rationale**: The parse endpoint is a general-purpose classifier; callers that don't need prediction market signals should not receive extra fields. A dedicated endpoint also lets us evolve the signal interface (add `market_question` semantic matching in v2) without touching parse response versioning.

**Alternative considered**: Add `p_model` as an optional response field on `POST /parse`. Rejected — conflates two concerns and makes the parse response harder to version cleanly.

### Decision: `p_model` is computed from (sentiment, impact_score, confidence) using a fixed formula in v1

Formula:
```
sentiment_adj = +0.25 if positive, -0.25 if negative, 0.0 if neutral
impact_weight  = 0.5 + impact_score * 0.5      # maps [0,1] → [0.5, 1.0]
raw_adj        = sentiment_adj * impact_weight * confidence
p_model        = clamp(0.5 + raw_adj, 0.05, 0.95)
```

Properties:
- **Neutral prior**: 0.5 when sentiment is neutral or confidence is 0
- **Bounded**: [0.05, 0.95] — never expresses false certainty
- **Deterministic**: given same inputs → same output (satisfies determinism requirement)
- **Monotone**: higher impact and higher confidence amplify the directional signal

**Rationale**: A simple, auditable formula that can be explained to risk-conscious users. The formula does not interpret the `market_question` — it measures how bullish or bearish the news is as a proxy for a "price goes up / event happens" prior.

**Alternative considered**: Always call the LLM to interpret `market_question`. Rejected for v1 — adds latency, cost, and non-determinism without clear accuracy gain until we have ground truth to calibrate against.

### Decision: `market_question` is accepted but not interpreted in v1

The field is stored for traceability and future use but does not influence `p_model` computation in v1. This keeps the API contract stable as we add semantic reasoning in v2.

### Decision: `market_direction` is always present on the signal response

Derived from `p_model`:
- `p_model > 0.55` → `bullish`
- `p_model < 0.45` → `bearish`
- else → `neutral`

The thresholds (±0.05) represent a low-conviction neutral band — meaningful when confidence is low or sentiment is ambiguous.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| `p_model` does not account for market_question semantics | Document clearly in API docs; v2 will add LLM-based question matching |
| Formula is a proxy, not a calibrated probability | Users should treat `p_model` as a directional signal, not a frequentist probability |
| A "positive" news item may be bearish for a specific market (e.g., bullish news for BTC is bearish for a "BTC crash" market) | Caller (n8n workflow) applies market-direction logic; `market_direction` field aids interpretation |

## Open Questions

- Should v2 support a `mode` parameter (`heuristic` vs `llm`) to let callers opt into LLM-based `p_model`?
- What calibration dataset do we use to validate formula accuracy against real Polymarket outcomes?
