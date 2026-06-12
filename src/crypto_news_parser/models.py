from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_TEXT_LENGTH = 20_000


class EventType(str, Enum):
    # Primary taxonomy: user's MECE buckets + explicit fallbacks.
    UNKNOWN = "UNKNOWN"
    MISC_OTHER = "MISC_OTHER"

    # Regulation, Policy & Government
    REGULATORY_ACTION_ENFORCEMENT = "REGULATORY_ACTION_ENFORCEMENT"
    LEGISLATION_POLICY_DEVELOPMENT = "LEGISLATION_POLICY_DEVELOPMENT"
    GOVERNMENT_CENTRAL_BANK_INITIATIVES = "GOVERNMENT_CENTRAL_BANK_INITIATIVES"

    # Institutions, Markets & Capital
    INSTITUTIONAL_ADOPTION_STRATEGY = "INSTITUTIONAL_ADOPTION_STRATEGY"
    CAPITAL_MARKETS_ACTIVITY = "CAPITAL_MARKETS_ACTIVITY"
    FUNDING_INVESTMENT_MA = "FUNDING_INVESTMENT_MA"
    MARKET_STRUCTURE_LIQUIDITY_SHIFTS = "MARKET_STRUCTURE_LIQUIDITY_SHIFTS"

    # Companies & Organizations
    COMPANY_FINANCIAL_PERFORMANCE = "COMPANY_FINANCIAL_PERFORMANCE"
    CORPORATE_GOVERNANCE_LEADERSHIP_CHANGES = "CORPORATE_GOVERNANCE_LEADERSHIP_CHANGES"
    BUSINESS_MODEL_STRATEGIC_PIVOT = "BUSINESS_MODEL_STRATEGIC_PIVOT"

    # Protocols, Networks & Technology
    PROTOCOL_UPGRADES_NETWORK_CHANGES = "PROTOCOL_UPGRADES_NETWORK_CHANGES"
    NEW_PROTOCOL_PRODUCT_LAUNCHES = "NEW_PROTOCOL_PRODUCT_LAUNCHES"
    INTEROPERABILITY_INFRA_DEVELOPMENTS = "INTEROPERABILITY_INFRA_DEVELOPMENTS"
    SECURITY_INCIDENTS_EXPLOITS = "SECURITY_INCIDENTS_EXPLOITS"

    # Assets, Tokens & Economics
    TOKEN_ECONOMICS_SUPPLY_EVENTS = "TOKEN_ECONOMICS_SUPPLY_EVENTS"
    STABLECOINS_MONETARY_MECHANICS = "STABLECOINS_MONETARY_MECHANICS"
    YIELD_RATES_RETURN_DYNAMICS = "YIELD_RATES_RETURN_DYNAMICS"

    # Ecosystem & Use-Cases
    RWA_DEVELOPMENTS = "RWA_DEVELOPMENTS"
    PAYMENTS_COMMERCE_CONSUMER_ADOPTION = "PAYMENTS_COMMERCE_CONSUMER_ADOPTION"
    ECOSYSTEM_PARTNERSHIPS_INTEGRATIONS = "ECOSYSTEM_PARTNERSHIPS_INTEGRATIONS"


class EventTypeV1(str, Enum):
    """Legacy v1 taxonomy (kept for best-effort mapping)."""

    UNKNOWN = "UNKNOWN"

    ETF_APPROVAL = "ETF_APPROVAL"
    ETF_REJECTION = "ETF_REJECTION"
    ETF_FILING = "ETF_FILING"
    ETF_INFLOW = "ETF_INFLOW"
    ETF_OUTFLOW = "ETF_OUTFLOW"

    ENFORCEMENT_ACTION = "ENFORCEMENT_ACTION"
    EXCHANGE_HACK = "EXCHANGE_HACK"

    STABLECOIN_ISSUANCE = "STABLECOIN_ISSUANCE"
    STABLECOIN_DEPEG = "STABLECOIN_DEPEG"

    CEX_INFLOW = "CEX_INFLOW"
    CEX_OUTFLOW = "CEX_OUTFLOW"

    PROTOCOL_UPGRADE = "PROTOCOL_UPGRADE"
    MINER_SHUTDOWN = "MINER_SHUTDOWN"


class Sentiment(str, Enum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"


class MarketDirection(str, Enum):
    bullish = "bullish"
    bearish = "bearish"
    neutral = "neutral"


class TimeHorizon(str, Enum):
    short_term = "short_term"
    medium_term = "medium_term"
    long_term = "long_term"


class Jurisdiction(str, Enum):
    US = "US"
    AMERICAS_NON_US = "AMERICAS_NON_US"
    EUROPE = "EUROPE"
    ASIA = "ASIA"
    AFRICA = "AFRICA"
    OCEANIA = "OCEANIA"
    GLOBAL = "GLOBAL"


class JurisdictionBasis(str, Enum):
    explicit = "explicit"
    implied = "implied"
    none = "none"


class ParseRequest(BaseModel):
    text: str = Field(..., description="Crypto-related text to parse")
    deterministic: bool = Field(False, description="If true, output is reproducible")

    # Optional caller-provided id to correlate parses and feedback.
    input_id: str | None = Field(
        None,
        description="Optional caller-provided identifier for correlating parses and feedback",
    )

    # Optional metadata (v1): accepted for traceability; MUST NOT trigger any fetching.
    source_url: str | None = Field(None, description="Optional source URL (not fetched)")
    source_name: str | None = Field(None, description="Optional source name/publisher")
    source_published_at: str | None = Field(
        None,
        description="Optional published timestamp as ISO 8601 string (not interpreted in v1)",
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if value is None:
            raise ValueError("text is required")
        value = value.strip()
        if not value:
            raise ValueError("text must be non-empty")
        return value

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if any(ch.isspace() for ch in value):
            raise ValueError("source_url must not contain whitespace")
        # Keep validation lightweight; accept absolute URLs/URIs (no fetching is performed).
        # This supports schemes like https://, http://, synthetic://, ipfs://, etc.
        if "://" not in value and not value.startswith("urn:"):
            raise ValueError("source_url must be an absolute URL/URI (e.g., https://...)")
        if len(value) > 2048:
            raise ValueError("source_url is too long")
        return value


class ParseUrlRequest(BaseModel):
    url: str = Field(..., description="Absolute http(s) URL to fetch and parse")
    deterministic: bool = Field(
        False, description="If true, output is reproducible given fetched content"
    )

    # Optional caller-provided id to correlate parses and feedback.
    input_id: str | None = Field(
        None,
        description="Optional caller-provided identifier for correlating parses and feedback",
    )

    # Optional metadata (traceability only).
    source_name: str | None = Field(None, description="Optional source name/publisher")
    source_published_at: str | None = Field(
        None,
        description="Optional published timestamp as ISO 8601 string (not interpreted in v1)",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value is None:
            raise ValueError("url is required")
        value = value.strip()
        if not value:
            raise ValueError("url must be non-empty")
        if any(ch.isspace() for ch in value):
            raise ValueError("url must not contain whitespace")
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        if len(value) > 2048:
            raise ValueError("url is too long")
        return value


class FeedbackRequest(BaseModel):
    parse_id: int | None = Field(
        default=None,
        description="Optional parse id returned in X-Parse-Id header when persistence is enabled",
    )
    input_id: str | None = Field(
        default=None,
        description=(
            "Optional caller-provided id to correlate feedback when parse_id is not available"
        ),
    )
    text: str | None = Field(
        default=None,
        description="Optional raw text (required for export when parse_id is not provided)",
    )
    expected: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Corrected fields (e.g., event_type, event_subtype, jurisdiction, assets, entities)"
        ),
    )
    notes: str | None = Field(default=None, description="Optional free-form notes")

    @field_validator("expected")
    @classmethod
    def validate_expected(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("expected must be an object")
        return value


class FeedbackResponse(BaseModel):
    feedback_id: int
    status: str = "stored"


class ParseResponse(BaseModel):
    event_type: EventType
    v1_event_type: EventTypeV1 | None = Field(
        default=None,
        description="Optional best-effort mapping to the legacy v1 event_type taxonomy",
    )
    event_subtype: str | None = Field(
        default=None,
        description="Optional finer-grained label consistent with event_type",
    )
    topics: list[str]
    assets: list[str]
    entities: list[str]
    jurisdiction: Jurisdiction
    jurisdiction_basis: JurisdictionBasis | None = Field(
        default=None,
        description=(
            'Optional explanation of how jurisdiction was determined: '
            '"explicit" | "implied" | "none".'
        ),
    )
    jurisdiction_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional confidence score for jurisdiction inference.",
    )
    sentiment: Sentiment
    impact_score: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)

    market_direction: MarketDirection | None = None
    systemic_risk: bool | None = None
    retail_relevant: bool | None = None
    time_horizon: TimeHorizon | None = None

    schema_version: str
    model_version: str


class RiskRequest(BaseModel):
    # Market inputs
    p_model: float = Field(
        ..., ge=0.0, le=1.0, description="Your estimated true probability (from /signal or manual)"
    )
    p_market: float = Field(
        ..., ge=0.01, le=0.99, description="Current market price (e.g. 0.55 on Polymarket)"
    )

    # Portfolio state
    bankroll: float = Field(..., gt=0, description="Total trading bankroll in USD")
    deployed: float = Field(0.0, ge=0.0, description="Capital already deployed in open positions")

    # Parser confidence — scales kelly_fraction down for low-confidence signals
    confidence: float = Field(
        1.0, ge=0.0, le=1.0, description="Parser confidence (0.0–1.0); scales kelly_fraction"
    )

    # Config overrides (optional — defaults are conservative starting values)
    min_edge: float = Field(0.04, ge=0.0, description="Minimum required edge (p_model - p_market)")
    kelly_fraction: float = Field(
        0.25, gt=0.0, le=1.0, description="Fractional Kelly multiplier (scaled by confidence)"
    )
    max_bet_fraction: float = Field(
        0.05, gt=0.0, le=1.0, description="Max single bet as fraction of bankroll"
    )
    max_exposure_fraction: float = Field(
        0.30, gt=0.0, le=1.0, description="Max total deployed as fraction of bankroll"
    )
    var_tolerance: float = Field(
        0.10, gt=0.0, le=1.0, description="Max acceptable VaR95 as fraction of bankroll"
    )

    # Time-to-expiry scaling — shorter horizon = more conviction = higher position
    days_to_expiry: int | None = Field(
        None,
        ge=1,
        description=(
            "Days until market resolution. When provided, scales Kelly fraction "
            "down for longer-horizon markets "
            "(day 1 = 1.0×, day 10 = 0.5×). Has no effect when None."
        ),
    )

    # Atomic capital reservation (server-side tracking)
    auto_reserve: bool = Field(
        False,
        description=(
            "If true and verdict=GO, atomically reserve bet_size in server-side deployed "
            "capital ledger. `deployed` field is ignored and the server reads current capital."
        ),
    )
    bet_id: str | None = Field(
        None, description="Caller-provided idempotency key; prevents double-reservation on retry"
    )


class RuleResult(BaseModel):
    name: str
    passed: bool
    value: float | None = None
    threshold: float | None = None
    message: str


class RiskResponse(BaseModel):
    verdict: str = Field(..., description="GO or NO_GO")
    bet_size: float | None = Field(
        None, description="Recommended bet size in USD (only present on GO)"
    )
    ev: float | None = Field(None, description="Expected value of the recommended bet in USD")
    kelly_full: float | None = Field(None, description="Full Kelly bet size for reference")
    rules: list[RuleResult]
    edge: float
    kelly_f: float = Field(..., description="Raw Kelly fraction f*")


class SignalRequest(BaseModel):
    text: str = Field(..., description="Crypto-related news text to derive a market signal from")
    market_question: str | None = Field(
        None,
        description=(
            "Optional prediction market question. "
            "Required for LLM-powered p_model estimation (when LLM_ENABLE=1); "
            "absence forces heuristic path even when LLM is enabled."
        ),
    )
    deterministic: bool = Field(False, description="If true, output is reproducible")
    input_id: str | None = Field(
        None,
        description="Optional caller-provided identifier for correlating requests",
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if value is None:
            raise ValueError("text is required")
        value = value.strip()
        if not value:
            raise ValueError("text must be non-empty")
        return value


class SignalResponse(BaseModel):
    p_model: float = Field(
        ..., ge=0.0, le=1.0, description="Directional probability signal (0.0–1.0)"
    )
    p_model_method: str = Field(
        ..., description='Computation path that produced p_model: "llm" or "heuristic"'
    )
    signal_prompt_version: str | None = Field(
        None, description="Prompt version used to produce p_model (None for heuristic path)"
    )
    market_direction: MarketDirection = Field(
        ..., description="bullish / bearish / neutral derived from p_model"
    )
    event_type: EventType
    sentiment: Sentiment
    impact_score: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    assets: list[str]
    jurisdiction: Jurisdiction
    schema_version: str
    model_version: str


class TrackMarketRequest(BaseModel):
    condition_id: str = Field(..., description="Polymarket condition ID for the market")
    question: str | None = Field(
        None, description="Human-readable market question (optional, for records)"
    )
    parse_id: int | None = Field(
        None, description="Optional parse_id to link this market to a parse run"
    )
    input_id: str | None = Field(None, description="Optional caller-provided id for correlation")


class TrackMarketResponse(BaseModel):
    market_id: int
    condition_id: str
    status: str = "tracked"


class PollResolutionItem(BaseModel):
    condition_id: str
    outcome: str
    feedback_id: int


class PollResolutionsResponse(BaseModel):
    resolved: list[PollResolutionItem]
    checked: int
    errors: list[str]


class FlowScanRequest(BaseModel):
    top_n: int = Field(20, ge=1, le=50, description="Number of top-volume markets to scan")
    max_days: int = Field(
        7, ge=1, le=90, description="Only scan markets resolving within this many days"
    )
    min_liquidity: float = Field(10_000, ge=0, description="Minimum market liquidity in USDC")
    condition_id: str | None = Field(
        None, description="Scan a single market by condition ID, bypassing the filters above"
    )
    max_wallets: int | None = Field(
        None,
        ge=10,
        le=2000,
        description=(
            "Cap metadata lookups to the top-N wallets by position size per market "
            "(the slow part of a scan). None = all wallets (full fidelity, can take "
            "15+ minutes on high-volume markets)."
        ),
    )


class FlowNewWallet(BaseModel):
    wallet_address: str
    side: str
    usdc_size: float
    wallet_age_days: int


class FlowMarketResult(BaseModel):
    market_id: str
    market_question: str
    signal_score: int = Field(..., ge=0, le=100)
    risk_tier: str = Field(..., description="LOW | MEDIUM | HIGH")
    dominant_side: str | None = Field(
        None, description="YES | NO, or null when no new-wallet activity"
    )
    dominant_side_usdc: float
    dominant_side_count: int
    dominant_side_count_pct: float
    new_wallet_count_yes: int
    new_wallet_count_no: int
    new_wallet_usdc_yes: float
    new_wallet_usdc_no: float
    new_wallet_count_total: int
    new_wallet_total_usdc: float
    recent_burst_pct: float
    p_market_at_scan: float | None = Field(
        None, description="Market YES implied probability at scan time"
    )
    days_to_resolution: int
    new_wallets: list[FlowNewWallet] = Field(default_factory=list)
    summary: str


class FlowScanResponse(BaseModel):
    results: list[FlowMarketResult]
    scanned: int
    stored: bool = Field(..., description="Whether scan rows were persisted")


class FlowCalibrationBucket(BaseModel):
    n: int
    wins: int
    win_rate: float | None
    avg_implied: float | None
    lift: float | None


class FlowCalibrationResponse(BaseModel):
    overall: FlowCalibrationBucket
    tiers: dict[str, FlowCalibrationBucket]
    excluded: int


class ErrorEnvelope(BaseModel):
    error: ErrorObject


class ErrorObject(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
