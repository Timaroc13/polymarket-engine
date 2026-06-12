## ADDED Requirements

### Requirement: AI independent p_model estimate
The n8n workflow SHALL include a Claude AI node that independently estimates the probability that the prediction market's YES outcome will occur, given the article text and market question, and returns this estimate as `p_ai` (float in [0.0, 1.0]).

The AI node SHALL receive:
- The article text (truncated to 1 500 characters)
- The Polymarket `market_question` string
- The heuristic `p_model` produced by `POST /signal` (for prompt context only; the AI estimate must be independently reasoned)

The AI node SHALL respond with a JSON object containing at minimum `p_ai` (float) and `reasoning` (string).

#### Scenario: AI node returns valid estimate
- **WHEN** the workflow reaches the AI cross-check node with `article_text`, `question`, and `p_model` available
- **THEN** the Claude API returns a JSON response containing `p_ai` in [0.0, 1.0]
- **AND** the `reasoning` field contains a non-empty string explaining the estimate

#### Scenario: AI node response is malformed
- **WHEN** the Claude API returns a response that cannot be parsed as JSON
- **THEN** the workflow Code node sets `ai_verdict = "PARSE_ERROR"`
- **AND** `p_ai` defaults to the heuristic `p_model` value
- **AND** the market is treated as if the AI agreed (not suppressed)

### Requirement: AI cross-check divergence detection
The workflow SHALL compute `divergence = |p_model - p_ai|` and classify the result as:
- `ai_verdict = "AGREE"` when `divergence ≤ 0.15`
- `ai_verdict = "DIVERGE"` when `divergence > 0.15`

Markets where `ai_verdict = "DIVERGE"` SHALL be dropped from the pipeline before the Edge Filter node. They SHALL NOT generate a Telegram alert.

The divergence threshold of 0.15 is the default and SHOULD be configurable via the n8n variable `CROSS_CHECK_THRESHOLD`.

#### Scenario: AI agrees with heuristic — market proceeds
- **WHEN** `p_model = 0.68` and `p_ai = 0.72`
- **THEN** `divergence = 0.04`, which is ≤ 0.15
- **AND** `ai_verdict = "AGREE"`
- **AND** the market proceeds to the Edge Filter node

#### Scenario: AI diverges from heuristic — market dropped
- **WHEN** `p_model = 0.70` and `p_ai = 0.40`
- **THEN** `divergence = 0.30`, which is > 0.15
- **AND** `ai_verdict = "DIVERGE"`
- **AND** the market is dropped; no Telegram alert is sent

#### Scenario: Custom divergence threshold respected
- **WHEN** `$vars.CROSS_CHECK_THRESHOLD = "0.20"` and `divergence = 0.17`
- **THEN** `ai_verdict = "AGREE"` because 0.17 ≤ 0.20
- **AND** the market proceeds to the Edge Filter node

### Requirement: AI cross-check fields included in alert
When a market passes the AI cross-check and ultimately produces a GO alert, the Telegram message SHALL include `p_ai` and `ai_verdict` so the recipient can inspect the AI's independent estimate alongside the heuristic `p_model`.

#### Scenario: Alert includes cross-check fields
- **WHEN** a market receives `ai_verdict = "AGREE"`, passes all risk rules, and generates a GO alert
- **THEN** the Telegram message body includes `p_ai` (the AI estimate) and `ai_verdict = "AGREE"`
