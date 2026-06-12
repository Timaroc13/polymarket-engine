# Capability: Market Signal (v1)

## ADDED Requirements

### Requirement: Signal endpoint
The system SHALL expose `POST /signal` which accepts news text and an optional market question and returns a schema-valid signal response including a `p_model` probability estimate and a `market_direction` label.

#### Scenario: Successful signal
- **WHEN** the client submits a valid JSON body containing a non-empty `text` string
- **THEN** the API returns HTTP 200
- **AND** the response includes `p_model` (float in [0.0, 1.0])
- **AND** the response includes `market_direction` (one of: bullish, bearish, neutral)
- **AND** the response includes supporting parse signals: `event_type`, `sentiment`, `impact_score`, `confidence`, `assets`, `jurisdiction`
- **AND** the response includes `schema_version` and `model_version`

#### Scenario: market_question accepted but not interpreted in v1
- **WHEN** the client includes a `market_question` string in the request
- **THEN** the API returns HTTP 200
- **AND** `p_model` is derived from news signals only (not from semantic matching against the question)

### Requirement: p_model derivation
The system SHALL compute `p_model` deterministically from the parsed event's sentiment, impact_score, and confidence using the following formula:

```
sentiment_adj  = +0.25 (positive) | -0.25 (negative) | 0.0 (neutral)
impact_weight  = 0.5 + impact_score * 0.5
raw_adj        = sentiment_adj * impact_weight * confidence
p_model        = clamp(0.5 + raw_adj, 0.05, 0.95)
```

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

### Requirement: market_direction derivation
The system SHALL derive `market_direction` from `p_model` using fixed thresholds:
- `p_model > 0.55` → `bullish`
- `p_model < 0.45` → `bearish`
- `0.45 ≤ p_model ≤ 0.55` → `neutral`

`market_direction` SHALL always be present (never null) in the signal response.

#### Scenario: High p_model maps to bullish
- **WHEN** the computed `p_model` is 0.72
- **THEN** `market_direction = "bullish"`

#### Scenario: Low p_model maps to bearish
- **WHEN** the computed `p_model` is 0.28
- **THEN** `market_direction = "bearish"`

#### Scenario: Mid-range p_model maps to neutral
- **WHEN** the computed `p_model` is 0.50
- **THEN** `market_direction = "neutral"`

### Requirement: Signal input constraints
The system SHALL enforce the same `text` size limit as `POST /parse` and return typed errors for constraint violations.

#### Scenario: Payload too large
- **WHEN** `text` exceeds the configured maximum length
- **THEN** the API returns HTTP 413
- **AND** the response matches the error schema

#### Scenario: Missing text
- **WHEN** the request JSON is well-formed but `text` is missing or empty
- **THEN** the API returns HTTP 422
- **AND** the response matches the error schema

#### Scenario: Non-JSON Content-Type
- **WHEN** the request `Content-Type` is not application/json
- **THEN** the API returns HTTP 415
- **AND** the response matches the error schema

### Requirement: Signal determinism
The system SHALL return the same `p_model` for the same `text` input when `deterministic = true`.

#### Scenario: Repeated deterministic call
- **WHEN** the same `text` is submitted with `deterministic = true` on two separate requests
- **THEN** both responses return identical `p_model` and `market_direction` values
