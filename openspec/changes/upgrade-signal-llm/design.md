# Design: Upgrade /signal to Claude-powered p_model

## Context

`POST /signal` currently computes `p_model` using a deterministic heuristic formula and ignores `market_question`. The `enhance-signal-pipeline` change worked around this limitation by adding a Claude AI cross-check in n8n after the parser responds. That approach solved the immediate accuracy problem but fragmented ownership: the parser and n8n each hold part of the `p_model` reasoning, and callers cannot easily tell which estimate was used.

This change moves Claude reasoning into the parser itself, making `POST /signal` the single source of truth. The n8n cross-check nodes become redundant and can be removed once the change is deployed.

## Goals / Non-Goals

**Goals**
- Replace `p_model` computation with a Claude Haiku call when `LLM_ENABLE=1` and `market_question` is supplied
- Keep the heuristic path intact as the default and as a fallback on any LLM failure
- Expose `p_model_method` so callers always know which path ran
- Extend, not bypass, the existing `LLMProvider` / `NoopLLMProvider` pattern
- Never crash due to an LLM error — always return a valid `SignalResponse`

**Non-Goals**
- Replacing the existing parse-refinement LLM call (that path uses `RefinementRequest` / `refine()`; this change adds a separate `estimate_p_model()` method)
- Adding LLM to `POST /parse` (parse stays heuristic by default)
- Changing the `POST /risk` or `POST /feedback` contracts
- Calibrating or retraining the heuristic formula from feedback
- Replacing Polymarket resolution polling

## Decisions

### Decision 1: Use Claude Haiku (`claude-haiku-4-5-20251001`)

**Rationale**: Haiku is the fastest and cheapest Claude model. The `/signal` endpoint has a p95 < 700ms target (from `project.md`). A Haiku call for a ~1 500-char prompt with a 256-token response budget typically completes in 400–600ms, which fits within budget alongside existing parse pipeline steps (~50–100ms). Larger models (Sonnet, Opus) offer marginal accuracy gains for a well-scoped binary probability prompt but would consistently exceed the latency target.

**Alternative considered**: `claude-sonnet-4-5` (better reasoning). Rejected — latency is likely to breach the 700ms target and cost is 5× higher per call at volume.

### Decision 2: Prompt returns `{"p_model": 0.XX, "reasoning": "one sentence"}` JSON only

Structured prompt design:
- **System prompt**: "You are an independent prediction market analyst. Given a news article and a binary market question, estimate the probability that the market resolves YES. Respond only with valid JSON in this exact format: {\"p_model\": <float between 0.0 and 1.0>, \"reasoning\": \"<one sentence>\"}. Do not include any other text."
- **User prompt**: "Market question: {market_question}\n\nArticle (truncated to 1500 chars):\n{text[:1500]}"
- **Max tokens**: 256 (sufficient for the JSON; prevents runaway output)

**Rationale for JSON-only response**: Easier to parse reliably than freeform text. The `reasoning` field is stored for traceability but not returned in the API response (keeps `SignalResponse` schema minimal).

**Alternative considered**: Return `p_model` as a bare float string. Rejected — a structured object is more robust to prompt drift and easier to validate.

### Decision 3: Add `estimate_p_model` to the LLMProvider protocol — do not bypass NoopLLMProvider

The existing adapter uses a `Protocol` (`LLMProvider`) with `refine()` for parse-stage refinement. The `NoopLLMProvider` implements it as a no-op, and `get_provider_from_env()` wires the correct implementation at startup.

This change adds `estimate_p_model(market_question: str, text: str) -> float | None` to the same protocol. `NoopLLMProvider.estimate_p_model` returns `None` (triggers heuristic). `ClaudeHaikuSignalProvider.estimate_p_model` makes the actual API call.

**Rationale**: Keeps the adapter pattern consistent. The `/signal` route calls `provider.estimate_p_model()` regardless of which provider is active — the fallback logic is inside the provider, not scattered across the route handler.

**Alternative considered**: A completely separate class hierarchy for the signal provider. Rejected — unnecessary indirection; the existing pattern is clean and the new method is a natural addition to the same protocol.

### Decision 4: Fallback on any LLM failure — never crash

If `ClaudeHaikuSignalProvider.estimate_p_model` encounters any of the following, it logs a warning at `WARNING` level and returns `None`:
- Network error / timeout
- Non-200 response from Claude API
- Response body is not valid JSON
- Parsed `p_model` is outside [0.0, 1.0]
- Any other uncaught exception

The route handler treats `None` as "use heuristic". This means the API always returns a valid `SignalResponse`, and `p_model_method = "heuristic"` tells the caller that the LLM path was not used.

**Rationale**: Prediction market signals are time-sensitive. A transient Claude API outage should not block the pipeline. The heuristic is a reasonable fallback — it was the sole source of truth before this change.

### Decision 5: `market_question` absence forces heuristic even when `LLM_ENABLE=1`

The LLM branch requires `market_question` to produce a meaningful probability estimate. If the caller omits it, the route falls back to the heuristic silently (no error, no `422`). `p_model_method = "heuristic"` in the response signals this.

`market_question` remains `str | None` in `SignalRequest` (no breaking schema change). The field description is updated to reflect the new semantics.

**Alternative considered**: Return `422` when `LLM_ENABLE=1` and `market_question` is absent. Rejected — would break existing callers who do not pass `market_question` and would only start failing after an operator enables the env var. Silent fallback is safer and consistent with the heuristic-first philosophy.

### Decision 6: Add `p_model_method` to `SignalResponse` as a required field

`p_model_method` is always `"llm"` or `"heuristic"`. Making it required (not optional) means callers can always branch on it without a null check. The field is new, so existing consumers that ignore unknown fields are unaffected.

**Alternative considered**: Optional field. Rejected — a required field is a stronger contract and avoids defensive null checks in consumers.

### Decision 7: n8n AI Cross-Check nodes are removed once this change is deployed

The three n8n nodes ("Build AI Request", "AI Cross-Check", "Parse AI Cross-Check") from `enhance-signal-pipeline` become redundant. They should be removed in the same deploy window as this change to avoid double-billing Claude API calls. The "AI Agrees" IF node and its false-branch drop logic are also removed. The workflow reverts to: "Parser Signal" → "Merge Signal" → "Edge Filter".

The feedback loop and crypto-only filter from `enhance-signal-pipeline` are unaffected — they are separate nodes and remain in place.

## Env Var Documentation

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_ENABLE` | No | `0` | Set to `1` to activate the Claude-powered `p_model` path on `POST /signal`. When unset or `0`, the heuristic formula is always used. |
| `ANTHROPIC_API_KEY` | When `LLM_ENABLE=1` | — | API key for the Anthropic Claude API. If absent when `LLM_ENABLE=1`, a warning is logged and the provider falls back to `NoopLLMProvider`. |
| `LLM_PROVIDER` | No | `none` | Reserved for future provider selection. Not used by this change. |

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Claude API latency pushes p95 above 700ms | Haiku is the fastest available model; text is truncated to 1 500 chars; max_tokens=256. Monitor p95 after rollout and consider async pre-fetch if needed. |
| Claude returns a `p_model` that is directionally wrong for the question | The `p_model_method = "llm"` field lets n8n log cases where the LLM estimate diverges from the resolved outcome, feeding a calibration dataset. |
| Prompt injection via `market_question` or `text` | Both fields are inserted as inert text in a user turn; the system prompt establishes the sole response format. No tool use or code execution is involved. |
| `ANTHROPIC_API_KEY` accidentally logged | The adapter must never log the key value; log only that the key is present/absent. |
| Cost increase at volume | Haiku pricing is low (~$0.25/1M input tokens). Each call uses ~500–700 input tokens. At 100 markets/cycle × 96 cycles/day = ~9 600 calls/day ≈ $1–2/day. Acceptable. |

## Open Questions

- Should `reasoning` from the Claude response be surfaced in `SignalResponse` as an optional field for debugging? (Current design: not exposed — keeps response schema minimal. Revisit if n8n consumers want it in Telegram alerts.)
- What is the latency budget breakdown between parse pipeline and LLM call? Consider adding per-step timing to the response headers (`X-Parse-Duration-Ms`, `X-LLM-Duration-Ms`) in a follow-up observability change.
- Should `market_question` be promoted to a required field on `SignalRequest` in a future version, now that it has semantic meaning on the LLM path?
