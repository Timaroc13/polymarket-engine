# Change: Upgrade /signal endpoint to use Claude for p_model estimation

## Why

The `POST /signal` endpoint currently computes `p_model` using a deterministic heuristic formula (`sentiment_adj × impact_weight × confidence`, clamped to [0.05, 0.95]). This formula has two structural limitations:

1. **It ignores the market question.** `market_question` is accepted but explicitly ignored in v1. A news article that is broadly bullish for crypto may still be bearish for a specific binary question (e.g., "Will BTC reach $100k by June?"). The heuristic has no way to express that nuance.
2. **Neutral sentiment always produces exactly 0.5.** This creates a dead zone where the signal provides no useful directional information, regardless of the article content.

The `enhance-signal-pipeline` change added an AI cross-check node in n8n to compensate for this by running an independent Claude estimate after the parser responds. That node adds latency, workflow complexity, and a third system to maintain (3 n8n nodes: "Build AI Request", "AI Cross-Check", "Parse AI Cross-Check"). Moving Claude reasoning into the parser makes the signal self-contained and removes the n8n dependency.

## What Changes

- **`POST /signal` (MODIFIED)**: When `LLM_ENABLE=1`, `p_model` and `market_direction` are derived from a Claude Haiku call that reasons about the market question and article text. When `LLM_ENABLE` is unset or `0`, behaviour is identical to current v1.
- **`SignalResponse` (MODIFIED)**: Add `p_model_method` field (`"llm"` | `"heuristic"`) so callers can identify which path was used without inspecting env vars.
- **`SignalRequest` (MODIFIED)**: `market_question` is promoted from "accepted for traceability" to "required for LLM path; optional for heuristic path". The field remains optional in the request schema; absence forces heuristic fallback even when `LLM_ENABLE=1`.
- **`llm_adapter.py` (MODIFIED)**: Extend the existing `LLMProvider` protocol and `NoopLLMProvider` pattern with a new `estimate_p_model` method. Add a `ClaudeHaikuSignalProvider` implementation (active when `LLM_ENABLE=1` and `ANTHROPIC_API_KEY` is set). The `NoopLLMProvider` returns `None` from `estimate_p_model`, triggering heuristic fallback.
- **n8n workflow (MODIFIED)**: Once this change is deployed, remove the 3 AI cross-check nodes ("Build AI Request", "AI Cross-Check", "Parse AI Cross-Check") from the predict-market-risk workflow. The parser signal is now the single source of truth for `p_model`.

## Impact

- Affected specs: `market-signal` (modified — `p_model_method` field, LLM path behaviour, `market_question` semantics)
- Affected code:
  - `src/crypto_news_parser/llm_adapter.py`: new `estimate_p_model` method on protocol + `ClaukeHaikuSignalProvider`
  - `src/crypto_news_parser/models.py`: `SignalResponse.p_model_method` field; `SignalRequest.market_question` doc update
  - `src/crypto_news_parser/main.py`: `/signal` route — LLM branch, fallback logic
  - `src/crypto_news_parser/parser.py`: `compute_market_signal()` unchanged (still used for heuristic fallback)
  - `tests/`: new test module for LLM path with mocked Claude responses
- **Non-breaking**: heuristic path is the default; existing callers not setting `LLM_ENABLE=1` see no behaviour change. `p_model_method` is a new optional field; existing consumers that do not read it are unaffected.
- **Non-goals**: Replacing the parse pipeline's existing refinement LLM call; adding LLM to `POST /parse`; changing the `POST /risk` or `POST /feedback` contracts; Polymarket resolution polling.
