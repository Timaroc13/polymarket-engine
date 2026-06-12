# Capability: Market Signal (v2 — LLM-powered p_model)

## MODIFIED Requirements

### Requirement: p_model derivation (replaces v1 heuristic-only requirement)

The system SHALL support two `p_model` computation paths, selected at runtime:

**LLM path** (active when `LLM_ENABLE=1` AND `market_question` is present in the request):
- The system SHALL call Claude Haiku (`claude-haiku-4-5-20251001`) with the `market_question` and article `text` (truncated to 1 500 chars)
- The prompt SHALL request a JSON response of the form `{"p_model": <float>, "reasoning": "<string>"}`
- The returned `p_model` SHALL be clamped to [0.0, 1.0] before use
- If the LLM call fails for any reason (network error, invalid JSON, out-of-range value), the system SHALL fall back to the heuristic formula without returning an error to the caller

**Heuristic path** (active when `LLM_ENABLE` is unset or `0`, OR when `market_question` is absent):
- The system SHALL compute `p_model` using the existing deterministic formula:
  ```
  sentiment_adj  = +0.25 (positive) | -0.25 (negative) | 0.0 (neutral)
  impact_weight  = 0.5 + impact_score * 0.5
  raw_adj        = sentiment_adj * impact_weight * confidence
  p_model        = clamp(0.5 + raw_adj, 0.05, 0.95)
  ```

#### Scenario: LLM path returns valid estimate
- **GIVEN** `LLM_ENABLE=1` is set and `ANTHROPIC_API_KEY` is configured
- **WHEN** the client submits a valid request with both `text` and `market_question`
- **THEN** the API returns HTTP 200
- **AND** `p_model` reflects the Claude estimate (not the heuristic formula)
- **AND** `p_model_method = "llm"` in the response

#### Scenario: LLM path falls back on failure
- **GIVEN** `LLM_ENABLE=1` is set
- **WHEN** the Claude API call fails (network error, malformed response, or out-of-range value)
- **THEN** the API returns HTTP 200 (no error surfaced to caller)
- **AND** `p_model` is derived from the heuristic formula
- **AND** `p_model_method = "heuristic"` in the response

#### Scenario: market_question absent forces heuristic even when LLM_ENABLE=1
- **GIVEN** `LLM_ENABLE=1` is set
- **WHEN** the client submits a request with `text` but without `market_question`
- **THEN** the API returns HTTP 200
- **AND** `p_model` is derived from the heuristic formula
- **AND** `p_model_method = "heuristic"` in the response

#### Scenario: Heuristic path (default)
- **GIVEN** `LLM_ENABLE` is unset or `0`
- **WHEN** the client submits any valid request
- **THEN** `p_model` is derived from the heuristic formula
- **AND** `p_model_method = "heuristic"` in the response

#### Scenario: Positive sentiment yields bullish p_model (heuristic path, unchanged)
- **WHEN** the parsed event has positive sentiment, impact_score = 1.0, and confidence = 1.0
- **THEN** the response includes `p_model` equal to 0.75
- **AND** `market_direction = "bullish"`
- **AND** `p_model_method = "heuristic"`

#### Scenario: Zero confidence collapses to neutral (heuristic path, unchanged)
- **WHEN** the parsed event has confidence = 0.0
- **THEN** the response includes `p_model` equal to 0.5
- **AND** `market_direction = "neutral"`
- **AND** `p_model_method = "heuristic"`

### Requirement: p_model_method field (NEW)

The system SHALL include a `p_model_method` field in every `SignalResponse`.

- Value SHALL be `"llm"` when the LLM path produced `p_model`
- Value SHALL be `"heuristic"` when the heuristic formula produced `p_model`
- The field SHALL always be present (never null or absent)

#### Scenario: p_model_method always present
- **WHEN** the client calls `POST /signal` under any conditions
- **THEN** the response includes `p_model_method` with value `"llm"` or `"heuristic"`

### Requirement: market_question semantics (MODIFIED)

The `market_question` field in `SignalRequest` SHALL be interpreted by the LLM when the LLM path is active.

- `market_question` SHALL remain optional in the request schema (type `str | None`)
- When present AND `LLM_ENABLE=1`: the system SHALL pass the field to Claude as the binary question to reason about
- When absent OR `LLM_ENABLE=0`: the field SHALL be accepted for traceability only (existing v1 behaviour)
- The system SHALL NOT require `market_question` for the request to succeed under any configuration

#### Scenario: market_question interpreted when LLM is enabled
- **GIVEN** `LLM_ENABLE=1`
- **WHEN** the client includes `market_question = "Will BTC exceed $100k before June 2026?"`
- **THEN** the Claude prompt uses that question as the target binary outcome
- **AND** `p_model` reflects Claude's YES-probability estimate for that specific question

#### Scenario: market_question ignored on heuristic path (backward-compatible)
- **GIVEN** `LLM_ENABLE` is unset
- **WHEN** the client includes `market_question` in the request
- **THEN** the field is accepted without error
- **AND** `p_model` is derived from the heuristic formula (question not used)
- **AND** `p_model_method = "heuristic"`

## UNCHANGED Requirements

The following requirements from the `add-market-signal` spec remain in force without modification:

### Requirement: Signal endpoint
The system SHALL expose `POST /signal` which accepts news text and an optional market question and returns a schema-valid signal response including a `p_model` probability estimate and a `market_direction` label.

### Requirement: market_direction derivation
The system SHALL derive `market_direction` from `p_model` using fixed thresholds:
- `p_model > 0.55` → `bullish`
- `p_model < 0.45` → `bearish`
- `0.45 ≤ p_model ≤ 0.55` → `neutral`

`market_direction` SHALL always be present (never null) in the signal response.

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

### Requirement: Signal determinism
When `LLM_ENABLE` is unset or `0`, the system SHALL return the same `p_model` for the same `text` input when `deterministic = true`. Determinism is not guaranteed on the LLM path.

#### Scenario: Repeated deterministic call (heuristic path)
- **GIVEN** `LLM_ENABLE` is unset or `0`
- **WHEN** the same `text` is submitted with `deterministic = true` on two separate requests
- **THEN** both responses return identical `p_model` and `market_direction` values

## REMOVED Requirements

### Requirement: market_question is not interpreted in v1 (REMOVED)

The following v1 constraint is retired by this change:
> "When the client includes a `market_question` string in the request, `p_model` is derived from news signals only (not from semantic matching against the question)."

This constraint is replaced by the conditional behaviour described above: `market_question` IS interpreted when `LLM_ENABLE=1`.
