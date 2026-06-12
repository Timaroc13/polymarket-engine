# Change: Enhance predict-market-risk signal pipeline with feedback loop, AI cross-check, and crypto-only market filter

## Why

The predict-market-risk pipeline currently produces a `p_model` estimate but discards ground truth once a Polymarket market resolves. This means the signal formula never improves. In addition, the heuristic formula can be overconfident or misaligned with the specific market question, and the pipeline runs on all Polymarket markets regardless of topic — including political, sports, and entertainment markets that the parser was never designed to handle.

Three targeted changes address these gaps:

1. **Feedback loop**: Feed resolved market outcomes back to the parser's existing `/feedback` endpoint so that the calibration gap between `p_model` and resolved truth accumulates over time and can be used to retune the signal formula.
2. **AI cross-check node**: Add a Claude AI reasoning node in the n8n workflow that independently estimates `p_model` from the article text and market question, then compares against the parser's heuristic `p_model` to surface hallucinations or overconfidence before an alert is sent.
3. **Crypto-only market filter**: Filter the Polymarket results to crypto-related markets before any expensive downstream calls (Tavily search, parser signal, AI cross-check), because the parser's event taxonomy and signal formula are crypto-specific.

## What Changes

- **`market-signal` capability (MODIFIED)**: Document that `p_model` MAY be submitted back to `POST /feedback` with a resolved outcome so the calibration record can be built over time. The feedback request shape already accepts a free-form `expected` dict; this change specifies the `p_model_resolved` key convention.
- **`ai-cross-check` capability (ADDED)**: A new n8n workflow node (Claude AI) that receives the article text and market question and returns an independent `p_ai` estimate and a cross-check verdict (`AGREE` / `DIVERGE`). The risk alert is suppressed or flagged when `|p_model - p_ai| > threshold`.
- **`polymarket-filter` capability (ADDED)**: The "Parse & Filter Markets" n8n code node is extended with a crypto keyword allow-list. Markets whose `question` field does not match any allow-list keyword are dropped before the Tavily search step.

**Non-breaking**: `POST /signal`, `POST /risk`, `POST /parse`, and `POST /feedback` API contracts are unchanged. All changes are additive within the n8n workflow and the feedback convention.

## Impact

- Affected specs: `market-signal` (modified — feedback convention), `ai-cross-check` (new), `polymarket-filter` (new)
- Affected systems:
  - n8n workflow (`Predict Market Risk Scanner ngrok working.json`): add crypto filter to "Parse & Filter Markets" node; add Claude AI cross-check node between "Merge Signal" and "Edge Filter"; add feedback dispatch node after market resolution
  - Parser API (`POST /feedback`): no code changes required; this change only formalises how the n8n workflow calls the existing endpoint
- **Non-goals**: Retraining or hot-patching the `p_model` formula from feedback at runtime (out of scope for this change); Polymarket resolution webhook / polling implementation (caller's responsibility — spec covers the feedback submission shape only); changing the parser's closed event taxonomy
