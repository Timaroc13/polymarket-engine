## MODIFIED Requirements

### Requirement: p_model derivation
The system SHALL compute `p_model` deterministically from the parsed event's sentiment, impact_score, and confidence using the following formula:

```
sentiment_adj  = +0.25 (positive) | -0.25 (negative) | 0.0 (neutral)
impact_weight  = 0.5 + impact_score * 0.5
raw_adj        = sentiment_adj * impact_weight * confidence
p_model        = clamp(0.5 + raw_adj, 0.05, 0.95)
```

When a Polymarket market resolves, callers SHOULD submit the resolved binary outcome back to `POST /feedback` using the following `expected` key convention:
- `p_model_resolved`: `1.0` if the YES outcome occurred, `0.0` if the NO outcome occurred
- `p_model_at_alert`: the `p_model` value that was used when the alert was generated
- `market_question`: the Polymarket question string for traceability

This feedback record enables offline calibration of the formula against ground truth. The parser stores the record as-is; no runtime formula update occurs.

#### Scenario: Positive sentiment yields bullish p_model
- **WHEN** the parsed event has positive sentiment, impact_score = 1.0, and confidence = 1.0
- **THEN** the response includes `p_model` equal to 0.75 (sentiment_adj=+0.25, impact_weight=1.0, raw_adj=0.25)
- **AND** `market_direction = "bullish"`

#### Scenario: Negative sentiment yields bearish p_model
- **WHEN** the parsed event has negative sentiment, impact_score = 1.0, and confidence = 1.0
- **THEN** the response includes `p_model` below 0.45
- **AND** `market_direction = "bearish"`

#### Scenario: Zero confidence collapses to neutral
- **WHEN** the parsed event has confidence = 0.0
- **THEN** the response includes `p_model` equal to 0.5
- **AND** `market_direction = "neutral"`

#### Scenario: Resolved outcome submitted via feedback
- **WHEN** a Polymarket market with `slug = "btc-above-100k-march"` resolves YES
- **AND** the caller POSTs to `POST /feedback` with body `{ "input_id": "btc-above-100k-march", "expected": { "p_model_resolved": 1.0, "p_model_at_alert": 0.68, "market_question": "Will BTC be above $100k on March 31?" } }`
- **THEN** the API returns HTTP 200 with a `feedback_id`
- **AND** the record is stored for offline calibration use
