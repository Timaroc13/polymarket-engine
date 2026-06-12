# Tasks: enhance-signal-pipeline

## 1. Crypto-only market filter (n8n workflow)

- [x] 1.1 Define the crypto keyword allow-list constant in the "Parse & Filter Markets" Code node
  - Add `const CRYPTO_KEYWORDS = [...]` at the top of the node's JS block (see `design.md` for seed list)
- [x] 1.2 Add keyword filtering logic before the existing volume/price filters
  - A market passes if `CRYPTO_KEYWORDS.some(kw => question.toLowerCase().includes(kw))` is true
  - Log dropped market count to n8n execution log via `console.log`
- [x] 1.3 Verify end-to-end: trigger workflow manually, confirm political/sports markets are absent from "Tavily Search" input items

## 2. AI cross-check node (n8n workflow)

- [x] 2.1 Add a Claude AI node (n8n Anthropic / HTTP Request to Claude API) between "Merge Signal" and "Edge Filter (≥ 4%)"
  - Model: `claude-haiku-4-5-20251001` (cheap, fast; configurable via `$vars.CLAUDE_MODEL`)
  - Max tokens: 256 (JSON response only)
  - System prompt: establish role as independent prediction market analyst
  - User prompt: inject `article_text` (truncated to 1 500 chars), `question`, `p_model`
- [x] 2.2 Add a "Parse AI Cross-Check" Code node immediately after the Claude node
  - Parse Claude's JSON response to extract `p_ai` (float)
  - Compute `divergence = Math.abs(p_model - p_ai)`
  - Set `ai_verdict`: `"AGREE"` if divergence ≤ 0.15, `"DIVERGE"` otherwise; `"PARSE_ERROR"` on JSON parse failure (treat as AGREE)
  - Pass through all existing fields plus `p_ai`, `divergence`, `ai_verdict`
- [x] 2.3 Add an IF node "AI Agrees" between "Parse AI Cross-Check" and "Edge Filter (≥ 4%)"
  - Pass-through condition: `ai_verdict !== "DIVERGE"`
  - Drop (false branch) items where `ai_verdict === "DIVERGE"` — no further action
- [x] 2.4 Update "Format Alert" Code node to include `p_ai` and `ai_verdict` in the Telegram message body

## 3. Feedback loop (n8n workflow)

- [x] 3.1 Add a "Submit Resolved Outcome" HTTP Request node that calls `POST /feedback`
  - Trigger: manual or scheduled after market resolution (see note below)
  - Body: `{ "input_id": <slug>, "expected": { "p_model_resolved": <0.0|1.0>, "p_model_at_alert": <p_model>, "market_question": <question> } }`
  - Auth: `Authorization: Bearer {{ $vars.PARSER_API_KEY }}` (if `REQUIRED_API_KEY` is set)
- [x] 3.2 Document (in a workflow sticky note) the resolution detection approach: caller polls Polymarket API for `resolved: true` and triggers the feedback node via an n8n webhook or separate schedule
- [x] 3.3 Verify parser accepts the feedback payload: manually POST to `/feedback` with `input_id` and `expected.p_model_resolved`; confirm HTTP 200 and `feedback_id` returned

## 4. Spec validation

- [x] 4.1 Run `openspec validate enhance-signal-pipeline --strict --no-interactive` and resolve all reported issues

## Notes

- The "Submit Resolved Outcome" node (task 3.1) is designed as a standalone sub-workflow or manual trigger. Automated resolution detection (polling Polymarket for `resolved: true`) is out of scope for this change and should be addressed in a follow-up `add-resolution-poller` change.
- All three changes are isolated additions. Existing nodes ("Fetch Polymarket Markets", "Parser Signal", "Parser Risk", "Send Telegram Alert") are not restructured.
