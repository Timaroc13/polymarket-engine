# Tasks: upgrade-signal-llm

## 1. LLM Adapter

- [x] 1.1 Add `estimate_p_model` method to the `LLMProvider` Protocol in `llm_adapter.py`
  - Signature: `async def estimate_p_model(self, market_question: str, text: str) -> float | None`
  - Returns a float in [0.0, 1.0] on success, or `None` to signal "fall back to heuristic"
- [x] 1.2 Update `NoopLLMProvider.estimate_p_model` to return `None` (always heuristic fallback)
- [x] 1.3 Add `ClaudeHaikuSignalProvider` class implementing `LLMProvider`
  - Model: `claude-haiku-4-5-20251001`
  - Max tokens: 256
  - Prompt template: system message establishes role as prediction market analyst; user message injects `market_question` and `text` (truncated to 1 500 chars); instructs response as `{"p_model": 0.XX, "reasoning": "one sentence"}` JSON only
  - Parse JSON response; extract `p_model` float; validate it is in [0.0, 1.0]
  - On any exception (network error, JSON parse failure, out-of-range value): log warning and return `None` — never raise
- [x] 1.4 Update `get_provider_from_env()` to return `ClaudeHaikuSignalProvider` when `LLM_ENABLE=1` and `ANTHROPIC_API_KEY` is set
  - If `LLM_ENABLE=1` but `ANTHROPIC_API_KEY` is absent: log a warning and return `NoopLLMProvider` (do not crash)
- [x] 1.5 Add `ANTHROPIC_API_KEY` and `LLM_ENABLE` to env var documentation (README and/or inline docstrings)

## 2. Models

- [x] 2.1 Add `p_model_method` field to `SignalResponse` in `models.py`
  - Type: `str` with allowed values `"llm"` | `"heuristic"`
  - Description: indicates which computation path produced `p_model`
  - Make the field required (not optional) so callers can always rely on it
- [x] 2.2 Update `SignalRequest.market_question` field description to reflect new semantics:
  - Required for LLM path (absence forces heuristic even when `LLM_ENABLE=1`)
  - Optional for heuristic path (no behaviour change)
  - The field remains `str | None` in the schema (backward-compatible)

## 3. Route

- [x] 3.1 Update `POST /signal` handler in `main.py` to implement LLM branch
  - After existing parse steps, check: `llm_provider is not None and req.market_question is not None`
  - If true: call `await llm_provider.estimate_p_model(req.market_question, req.text)`
    - If result is a valid float: use it as `p_model`; derive `market_direction` from thresholds; set `p_model_method = "llm"`
    - If result is `None` (fallback): use `compute_market_signal()` heuristic; set `p_model_method = "heuristic"`
  - If false (no LLM or no market_question): use `compute_market_signal()` heuristic; set `p_model_method = "heuristic"`
- [x] 3.2 Ensure `p_model_method` is always populated in `SignalResponse`

## 4. Tests

- [x] 4.1 Add `tests/test_signal_llm.py` with unit tests for the LLM path using mocked Claude responses
  - Mock `ClaudeHaikuSignalProvider.estimate_p_model` to return a valid float (e.g., 0.72)
  - Assert response `p_model == 0.72` and `market_direction == "bullish"` and `p_model_method == "llm"`
- [x] 4.2 Test fallback behaviour: mock `estimate_p_model` to return `None`
  - Assert `p_model_method == "heuristic"` and `p_model` matches formula output
- [x] 4.3 Test fallback when `market_question` is absent: even with LLM enabled, assert `p_model_method == "heuristic"`
- [x] 4.4 Test invalid LLM response (raise exception inside mock): assert `p_model_method == "heuristic"` (no crash)
- [x] 4.5 Confirm existing `test_signal.py` tests still pass (heuristic path is unchanged when `LLM_ENABLE` is unset)

## 5. Documentation

- [x] 5.1 Update README to document `LLM_ENABLE` and `ANTHROPIC_API_KEY` env vars and their effect on `POST /signal`
- [x] 5.2 Document `p_model_method` in the README's response schema table for `POST /signal`
- [x] 5.3 Add a note that once this change is deployed, the three n8n AI cross-check nodes ("Build AI Request", "AI Cross-Check", "Parse AI Cross-Check") should be removed from the predict-market-risk workflow

## 6. Validation

- [x] 6.1 Run `openspec validate upgrade-signal-llm --strict --no-interactive` and resolve all reported issues
