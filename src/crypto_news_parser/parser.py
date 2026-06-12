from __future__ import annotations

import re
from dataclasses import dataclass

from .models import EventType, Jurisdiction, MarketDirection, Sentiment


@dataclass(frozen=True)
class CandidateEvent:
    event_type: EventType
    confidence: float
    impact_score: float


_PRECEDENCE: dict[EventType, int] = {
    # Lower number = higher precedence (used only for tie-breaks).
    EventType.SECURITY_INCIDENTS_EXPLOITS: 1,
    EventType.REGULATORY_ACTION_ENFORCEMENT: 2,
    EventType.STABLECOINS_MONETARY_MECHANICS: 3,
    EventType.PROTOCOL_UPGRADES_NETWORK_CHANGES: 4,
    EventType.CAPITAL_MARKETS_ACTIVITY: 5,
    EventType.MARKET_STRUCTURE_LIQUIDITY_SHIFTS: 6,
    EventType.FUNDING_INVESTMENT_MA: 7,
    EventType.INSTITUTIONAL_ADOPTION_STRATEGY: 8,
    EventType.NEW_PROTOCOL_PRODUCT_LAUNCHES: 9,
    EventType.INTEROPERABILITY_INFRA_DEVELOPMENTS: 10,
    EventType.RWA_DEVELOPMENTS: 11,
    EventType.PAYMENTS_COMMERCE_CONSUMER_ADOPTION: 12,
    EventType.ECOSYSTEM_PARTNERSHIPS_INTEGRATIONS: 13,
    EventType.LEGISLATION_POLICY_DEVELOPMENT: 14,
    EventType.GOVERNMENT_CENTRAL_BANK_INITIATIVES: 15,
    EventType.COMPANY_FINANCIAL_PERFORMANCE: 16,
    EventType.CORPORATE_GOVERNANCE_LEADERSHIP_CHANGES: 17,
    EventType.BUSINESS_MODEL_STRATEGIC_PIVOT: 18,
    EventType.TOKEN_ECONOMICS_SUPPLY_EVENTS: 19,
    EventType.YIELD_RATES_RETURN_DYNAMICS: 20,
    EventType.MISC_OTHER: 98,
    EventType.UNKNOWN: 99,
}


_TICKER_RE = re.compile(r"(?:\$)?([A-Z]{2,6})\b")


_ASSET_ALLOWLIST = {
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "BNB",
    "ADA",
    "DOGE",
    "LTC",
    "AVAX",
    "DOT",
    "LINK",
    "UNI",
    "AAVE",
    "USDT",
    "USDC",
}


_ASSET_NAME_PATTERNS: list[tuple[str, str]] = [
    (r"\bbitcoin(?:'s|’s)?\b", "BTC"),
    (r"\bethereum(?:'s|’s)?\b", "ETH"),
    (r"\bether(?:'s|’s)?\b", "ETH"),
    (r"\bsolana(?:'s|’s)?\b", "SOL"),
    (r"\btether(?:'s|’s)?\b", "USDT"),
    (r"\busdc\b", "USDC"),
]


_ENTITY_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’\-\.]*")


_ENTITY_SINGLE_WORD_ALLOW = {
    # Common entities from the current golden set / crypto news.
    "Bybit",
    "Ledger",
    "Robinhood",
    "BlackRock",
    "CoinDesk",
    "Cointelegraph",
    "Reuters",
}


_ENTITY_SINGLE_WORD_DENY = {
    # Generic sentence starters and common nouns.
    "A",
    "An",
    "The",
    "This",
    "That",
    "These",
    "Those",
    "Crypto",
    "Trading",
    "Tokenized",
    "Digital",
    "New",
    "York",
    "White",
    "House",
}


_ENTITY_ALLCAPS_DENY = {
    # Avoid duplicating assets/jurisdiction/regulators as entities.
    "USD",
    "US",
    "UAE",
    "EU",
    "UK",
    "SEC",
    "CFTC",
    "DOJ",
    "ETF",
    "IBAN",
}


_ENTITY_CONNECTORS = {"of", "the", "and", "for"}


def _is_crypto_related(text: str) -> bool:
    t = text.lower()
    crypto_cues = [
        "crypto",
        "cryptocurrency",
        "blockchain",
        "bitcoin",
        "ethereum",
        "stablecoin",
        "cbdc",
        "central bank digital currency",
        "token",
        "defi",
        "exchange",
        "wallet",
        "web3",
        "onchain",
        "mainnet",
        "testnet",
        "devnet",
        "layer-1",
        "layer 1",
        "layer-2",
        "layer 2",
        "staking",
        "validator",
    ]
    if any(cue in t for cue in crypto_cues):
        return True
    # If we extracted a known asset ticker, treat as crypto-related.
    if any(ticker in extract_assets(text) for ticker in _ASSET_ALLOWLIST):
        return True
    return False


def _clean_entity_token(token: str) -> str:
    t = token.strip("\"'“”‘’.,;:()[]{}")
    # Normalize possessives like “Saylor’s” -> “Saylor”.
    if t.endswith("'s") or t.endswith("’s"):
        t = t[:-2]
    return t


def _is_title_token(token: str) -> bool:
    if not token:
        return False
    if not token[0].isupper():
        return False
    # Require at least one lowercase to avoid treating tickers as Title Case.
    return any(c.islower() for c in token[1:])


def _is_allcaps_token(token: str) -> bool:
    if not token:
        return False
    return token.isupper() and any(c.isalpha() for c in token) and len(token) >= 2


def extract_assets(text: str) -> list[str]:
    # Best-effort: favor precision over recall.
    # 1) Map common asset names to tickers.
    t = text.lower()
    assets: list[str] = []
    for pattern, ticker in _ASSET_NAME_PATTERNS:
        if re.search(pattern, t):
            assets.append(ticker)

    # 2) Capture explicit tickers (e.g., BTC, $BTC) but only from an allowlist.
    for match in _TICKER_RE.finditer(text):
        token = match.group(1)
        if token in _ASSET_ALLOWLIST:
            assets.append(token)
    # de-dupe preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for a in assets:
        if a not in seen:
            ordered.append(a)
            seen.add(a)
    return ordered


def extract_entities(text: str) -> list[str]:
    # Conservative heuristic: extract sequences of capitalized tokens.
    tokens: list[str] = []
    for m in _ENTITY_TOKEN_RE.finditer(text):
        cleaned = _clean_entity_token(m.group(0))
        if cleaned:
            tokens.append(cleaned)

    entities: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        next_tok = tokens[i + 1] if i + 1 < len(tokens) else ""

        starts = _is_title_token(tok) or (
            _is_allcaps_token(tok) and tok not in _ENTITY_ALLCAPS_DENY and _is_title_token(next_tok)
        )
        if not starts:
            i += 1
            continue

        phrase_tokens: list[str] = [tok]
        i += 1

        while i < len(tokens):
            t = tokens[i]
            if (
                t.lower() in _ENTITY_CONNECTORS
                and i + 1 < len(tokens)
                and _is_title_token(tokens[i + 1])
            ):
                phrase_tokens.append(t)
                i += 1
                continue
            if _is_title_token(t):
                phrase_tokens.append(t)
                i += 1
                continue
            break

        phrase = " ".join(phrase_tokens).strip()
        if not phrase:
            continue

        # Filter very short or generic results.
        if " " not in phrase:
            if phrase in _ENTITY_SINGLE_WORD_DENY:
                continue
            if phrase not in _ENTITY_SINGLE_WORD_ALLOW:
                # Avoid picking up random single capitalized words.
                continue

        entities.append(phrase)

    # de-dupe preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for e in entities:
        if e not in seen:
            ordered.append(e)
            seen.add(e)
    return ordered


def resolve_jurisdiction(text: str) -> Jurisdiction:
    jurisdiction, _, _ = resolve_jurisdiction_with_meta(text)
    return jurisdiction


def resolve_jurisdiction_with_meta(text: str) -> tuple[Jurisdiction, str, float]:
    """Return (jurisdiction, basis, confidence).

    - basis: "explicit" | "implied" | "none"
    - confidence: float in [0, 1]

    This keeps the existing Jurisdiction enum stable while exposing whether
    geo was explicit vs inferred.
    """

    t = text.lower()

    def has_any(*patterns: str) -> bool:
        return any(re.search(p, t) for p in patterns)

    # Explicit global scope signals.
    if has_any(
        r"\bglobal\b",
        r"\bworldwide\b",
        r"\binternational\b",
        r"\bacross\s+(?:the\s+)?world\b",
    ):
        return Jurisdiction.GLOBAL, "explicit", 0.85

    # Explicit cues; otherwise GLOBAL.
    if has_any(
        r"\bunited states\b",
        r"\bu\.?s\.?\b",
        r"\bwhite house\b",
        r"\bsec\b",
        r"\bcftc\b",
        r"\bdoj\b",
        r"\bnyse\b",
        r"\bnasdaq\b",
    ):
        return Jurisdiction.US, "explicit", 0.9

    if has_any(
        r"\beuropean union\b",
        r"\beu\b",
        r"\beuropean\b",
        r"\besma\b",
        r"\bmica\b",
        r"\becb\b",
        r"\bunited kingdom\b",
        r"\buk\b",
        r"\bfca\b",
        r"\brussia\b",
        r"\brussian\b",
        r"\bmoscow\b",
        r"\bbrussels\b",
    ):
        return Jurisdiction.EUROPE, "explicit", 0.9

    # Currency cues (implied): treat € as EUROPE.
    if "€" in text:
        return Jurisdiction.EUROPE, "implied", 0.7

    if has_any(
        r"\bcanada\b",
        r"\bmexico\b",
        r"\bbrazil\b",
        r"\bargentina\b",
        r"\bchile\b",
        r"\bosc\b",
        r"\bcsa\b",
    ):
        return Jurisdiction.AMERICAS_NON_US, "explicit", 0.9

    if has_any(
        r"\bjapan\b",
        r"\bsingapore\b",
        r"\bhong\s+kong\b",
        r"\b(korea|south korea|north korea)\b",
        r"\bindia\b",
        r"\bchina\b",
        r"\buae\b",
        r"\bunited arab emirates\b",
        r"\bdubai\b",
        r"\babu dhabi\b",
    ):
        return Jurisdiction.ASIA, "explicit", 0.9

    if has_any(r"\baustralia\b", r"\bnew zealand\b"):
        return Jurisdiction.OCEANIA, "explicit", 0.9

    if has_any(r"\bnigeria\b", r"\bkenya\b", r"\bsouth africa\b"):
        return Jurisdiction.AFRICA, "explicit", 0.9

    return Jurisdiction.GLOBAL, "none", 0.3


def infer_sentiment(text: str) -> Sentiment:
    t = text.lower()
    negative_cues = [
        "hack",
        "exploit",
        "lawsuit",
        "charges",
        "indict",
        "ban",
        "depeg",
        # Market-negative language
        "sell-off",
        "selloff",
        "crash",
        "plunge",
        "plunged",
        "dump",
        "slump",
        "slumped",
        "meltdown",
        "risk sell-off",
        "down ",
        "fell",
        "falling",
    ]
    positive_cues = [
        "approval",
        "approved",
        "inflows",
        "record",
        "surge",
        "rally",
        "partnership",
    ]

    has_negative = any(k in t for k in negative_cues)
    has_positive = any(k in t for k in positive_cues)

    if has_negative and has_positive:
        return Sentiment.neutral
    if has_negative:
        return Sentiment.negative
    if has_positive:
        return Sentiment.positive
    return Sentiment.neutral


def infer_event_subtype(text: str, event_type: EventType) -> str | None:
    """Best-effort optional subtype, consistent with the selected event_type.

    This intentionally favors precision over recall.
    """

    t = text.lower()

    if event_type == EventType.REGULATORY_ACTION_ENFORCEMENT:
        # Use word boundaries to avoid false positives (e.g., "issued" contains "sued").
        if re.search(r"\b(lawsuit|sue|sues|sued)\b", t):
            return "regulation.enforcement.lawsuit"
        if re.search(r"\b(fine|fined|penalty|penalties|civil\s+penalty)\b", t):
            return "regulation.enforcement.fine"
        if any(w in t for w in ["settlement", "settled"]):
            return "regulation.enforcement.settlement"
        if any(w in t for w in ["ban", "banned", "prohibit", "prohibited", "restriction"]):
            return "regulation.restriction"
        if any(w in t for w in ["investigation", "probe"]):
            return "regulation.enforcement.investigation"
        if any(w in t for w in ["cease and desist", "cease-and-desist", "c&d"]):
            return "regulation.enforcement.cease_and_desist"
        return None

    if event_type == EventType.LEGISLATION_POLICY_DEVELOPMENT:
        if any(w in t for w in ["meeting", "summit", "hearing", "roundtable"]):
            return "regulation.policy.meeting"
        if any(
            w in t
            for w in [
                "bill",
                "draft bill",
                "executive order",
                "policy",
                "framework",
                "consultation",
            ]
        ):
            return "regulation.policy"
        if any(w in t for w in ["guidance", "clarified", "clarifies", "rules"]):
            return "regulation.guidance"
        return None

    if event_type == EventType.GOVERNMENT_CENTRAL_BANK_INITIATIVES:
        return "government.initiative"

    if event_type == EventType.NEW_PROTOCOL_PRODUCT_LAUNCHES:
        if "stablecoin" in t:
            if "registered" in t or "registration" in t:
                return "stablecoin.launch.registered"
            return "stablecoin.launch"
        if "mainnet" in t and any(w in t for w in ["launch", "launched", "launches"]):
            return "protocol.launch.mainnet"
        return "protocol.launch"

    if event_type == EventType.PROTOCOL_UPGRADES_NETWORK_CHANGES:
        if "hard fork" in t:
            return "protocol.upgrade.hard_fork"
        if "mainnet" in t and "upgrade" in t:
            return "protocol.upgrade.mainnet"
        if "upgrade" in t:
            return "protocol.upgrade.upgrade"
        if any(w in t for w in ["validator", "validators"]) and any(
            w in t for w in ["decline", "dropped", "fallen", "drop", "down"]
        ):
            return "network.validators.decline"
        return None

    if event_type == EventType.SECURITY_INCIDENTS_EXPLOITS:
        if "breach" in t:
            return "security.breach"
        if "exploit" in t:
            return "security.exploit"
        if any(w in t for w in ["validator", "validators"]) and any(
            w in t for w in ["failure", "failed", "slash", "slashed", "outage"]
        ):
            return "security.validator_failure"
        return "security.incident"

    if event_type == EventType.FUNDING_INVESTMENT_MA:
        if any(w in t for w in ["acquired", "acquisition", "merge", "merged", "merger"]):
            return "institutions.ma"
        return "institutions.funding"

    if event_type == EventType.INSTITUTIONAL_ADOPTION_STRATEGY:
        if ("bitcoin" in t or "btc" in t) and any(
            w in t
            for w in [
                "purchased",
                "purchase",
                "buys",
                "bought",
                "acquired",
                "added",
            ]
        ):
            return "institutions.treasury.btc_purchase"
        return "institutions.adoption"

    if event_type == EventType.CAPITAL_MARKETS_ACTIVITY:
        if "ipo" in t and any(w in t for w in ["filed", "filing", "f-1", "s-1"]):
            return "capital_markets.ipo.filing"
        if "ipo" in t and any(w in t for w in ["plans", "planning", "considering", "exploring"]):
            return "capital_markets.ipo.planning"
        # Allow IPO-debut inference without literal "ipo" if the story says
        # "market debut" on a major exchange.
        debut_words = ["market debut", "first day of trading", "began trading", "priced", "debut"]
        if any(w in t for w in debut_words):
            if any(w in t for w in ["nyse", "nasdaq"]):
                return "capital_markets.ipo.market_debut"
        if "ipo" in t and any(w in t for w in ["debut", "began trading", "priced", "listed"]):
            return "capital_markets.ipo.market_debut"
        if any(w in t for w in ["listing", "listed on", "up-list", "uplisting", "up listing"]):
            return "capital_markets.listing"
        if any(w in t for w in ["delist", "delisted", "delisting"]):
            return "capital_markets.delisting"
        return None

    if event_type == EventType.MARKET_STRUCTURE_LIQUIDITY_SHIFTS:
        if "tvl" in t or "total value locked" in t:
            return "market_structure.tvl"
        if "stablecoin" in t and any(w in t for w in ["supply", "issuance", "mint", "burn"]):
            return "market_structure.stablecoin_supply"
        if "exchange" in t and any(
            w in t
            for w in [
                "product",
                "launched",
                "roll out",
                "rolled out",
                "derivatives",
                "options",
            ]
        ):
            return "market_structure.exchange.product_expansion"
        if any(
            w in t
            for w in [
                "volatility",
                "sell-off",
                "selloff",
                "plunge",
                "dump",
                "rally",
                "surge",
                "crash",
            ]
        ):
            return "markets.volatility"
        return None

    if event_type == EventType.COMPANY_FINANCIAL_PERFORMANCE:
        if "reserve" in t or "reserves" in t:
            return "company.reserves"
        return "company.financials"

    if event_type == EventType.CORPORATE_GOVERNANCE_LEADERSHIP_CHANGES:
        if any(w in t for w in ["ceo", "cfo", "chair", "board"]):
            return "company.leadership"
        if any(w in t for w in ["layoff", "layoffs", "restructuring"]):
            return "company.restructuring"
        return "company.governance"

    if event_type == EventType.BUSINESS_MODEL_STRATEGIC_PIVOT:
        return "company.pivot"

    if event_type == EventType.TOKEN_ECONOMICS_SUPPLY_EVENTS:
        if any(w in t for w in ["unlock", "vesting"]):
            return "tokenomics.unlock"
        if any(w in t for w in ["burn", "burned"]):
            return "tokenomics.burn"
        if any(w in t for w in ["mint", "minted", "emissions"]):
            return "tokenomics.mint"
        return "tokenomics.supply"

    if event_type == EventType.STABLECOINS_MONETARY_MECHANICS:
        if "depeg" in t or "lost its peg" in t or "lost the peg" in t:
            return "stablecoin.depeg"
        if any(w in t for w in ["reserve", "reserves", "attestation", "audit", "backing"]):
            return "stablecoin.reserves.update"
        if any(w in t for w in ["yield", "yield model"]):
            return "stablecoin.yield"
        if any(w in t for w in ["warning", "warned", "risk", "threat", "impact"]):
            return "stablecoin.risk.warning"
        return "stablecoin.monetary_mechanics"

    if event_type == EventType.YIELD_RATES_RETURN_DYNAMICS:
        if any(w in t for w in ["apy", "yield", "staking"]):
            return "yield.staking"
        return "yield.rates"

    if event_type == EventType.RWA_DEVELOPMENTS:
        if "tokenized" in t:
            return "rwa.tokenization"
        return "rwa.development"

    if event_type == EventType.PAYMENTS_COMMERCE_CONSUMER_ADOPTION:
        return "payments.adoption"

    if event_type == EventType.INTEROPERABILITY_INFRA_DEVELOPMENTS:
        if "bridge" in t:
            return "infrastructure.bridge"
        if "wallet" in t:
            return "infrastructure.wallet"
        if any(w in t for w in ["cross-chain", "cross chain", "messaging"]):
            return "infrastructure.cross_chain"
        return "infrastructure.development"

    if event_type == EventType.ECOSYSTEM_PARTNERSHIPS_INTEGRATIONS:
        return "ecosystem.partnership"

    if event_type == EventType.MISC_OTHER:
        # Preserve some high-signal subtypes for common crypto narratives.
        if any(w in t for w in ["hack", "exploit", "breach"]):
            if "breach" in t:
                return "security.exchange_hack.breach"
            if "exploit" in t:
                return "security.exchange_hack.exploit"
            return "security.exchange_hack.hack"
        if "hard fork" in t:
            return "protocol.upgrade.hard_fork"
        if "mainnet" in t and "upgrade" in t:
            return "protocol.upgrade.mainnet"
        if "upgrade" in t:
            return "protocol.upgrade.upgrade"
        if "miner" in t and any(w in t for w in ["shutdown", "shut down", "halt"]):
            if "halt" in t:
                return "protocol.mining.halt"
            return "protocol.mining.shutdown"
        return "misc"

    if event_type == EventType.UNKNOWN:
        return None

    return None


def compute_market_signal(
    sentiment: Sentiment,
    impact_score: float,
    confidence: float,
) -> tuple[float, MarketDirection]:
    """Derive (p_model, market_direction) from parsed event signals.

    Formula (from design.md):
        sentiment_adj  = +0.25 | -0.25 | 0.0
        impact_weight  = 0.5 + impact_score * 0.5   # maps [0,1] → [0.5, 1.0]
        raw_adj        = sentiment_adj * impact_weight * confidence
        p_model        = clamp(0.5 + raw_adj, 0.05, 0.95)

    Thresholds for market_direction:
        p_model > 0.55  → bullish
        p_model < 0.45  → bearish
        else            → neutral
    """
    sentiment_map = {
        Sentiment.positive: 0.25,
        Sentiment.negative: -0.25,
        Sentiment.neutral: 0.0,
    }
    sentiment_adj = sentiment_map[sentiment]
    impact_weight = 0.5 + impact_score * 0.5
    raw_adj = sentiment_adj * impact_weight * confidence
    p_model = max(0.05, min(0.95, 0.5 + raw_adj))

    if p_model > 0.55:
        direction = MarketDirection.bullish
    elif p_model < 0.45:
        direction = MarketDirection.bearish
    else:
        direction = MarketDirection.neutral

    return p_model, direction


def _candidates(text: str) -> list[CandidateEvent]:
    t = text.lower()
    candidates: list[CandidateEvent] = []

    def add(event_type: EventType, confidence: float, impact_score: float) -> None:
        candidates.append(
            CandidateEvent(
                event_type=event_type,
                confidence=confidence,
                impact_score=impact_score,
            )
        )

    regulator_re = re.compile(r"\b(sec|cftc|doj|finra|fca|esma|ofac|regulator)\b")

    # Security incidents & exploits
    if any(w in t for w in ["hack", "exploit", "breach", "bug", "drained", "compromised"]):
        add(EventType.SECURITY_INCIDENTS_EXPLOITS, confidence=0.74, impact_score=0.9)

    # Regulatory action & enforcement
    enforcement_re = re.compile(
        r"\b(lawsuit|sues|sued|charges|charged|indict|indicted|fine|fined|penalty|penalties|settlement|investigation|probe|c&d)\b"
        r"|\bcease\s+and\s+desist\b|\bcease-and-desist\b"
    )

    if enforcement_re.search(t) and regulator_re.search(t):
        add(EventType.REGULATORY_ACTION_ENFORCEMENT, confidence=0.72, impact_score=0.85)

    # Legislation & policy development
    policy_words = [
        "draft bill",
        "consultation",
        "executive order",
        "policy framework",
        "framework",
        "guidance",
        "clarified",
        "clarifies",
        "rules",
        "hearing",
        "roundtable",
        "summit",
        "meeting",
    ]

    has_legislation_language = any(w in t for w in policy_words) or (
        re.search(r"\b(draft\s+)?bill(s)?\b", t) is not None
        and re.search(r"\b(treasury\s+bill(s)?|t-?bill(s)?|tbill(s)?)\b", t) is None
    )

    if has_legislation_language and (_is_crypto_related(text) or regulator_re.search(t)):
        add(EventType.LEGISLATION_POLICY_DEVELOPMENT, confidence=0.66, impact_score=0.65)

    # Government & central bank initiatives
    gov_words = [
        "cbdc",
        "central bank digital currency",
        "tokenization pilot",
        "public-sector",
        "public sector",
        "treasury",
        "ministry",
        "central bank",
    ]
    if any(w in t for w in gov_words) and _is_crypto_related(text):
        add(EventType.GOVERNMENT_CENTRAL_BANK_INITIATIVES, confidence=0.62, impact_score=0.6)

    # Capital markets activity
    if any(w in t for w in ["ipo", "spac", "public offering", "market debut"]):
        add(EventType.CAPITAL_MARKETS_ACTIVITY, confidence=0.66, impact_score=0.6)
        # Avoid false positives where "listed" is just an adjective
        # ("a listed company reported revenue...").
    if (
        any(w in t for w in ["listing", "delist", "delisted", "delisting"])
        or re.search(r"\blisted\s+on\b", t)
        or re.search(r"\b(up-list|uplisting|up listing)\b", t)
    ):
        add(EventType.CAPITAL_MARKETS_ACTIVITY, confidence=0.6, impact_score=0.55)

    # Funding, investment & M&A
    if any(
        w in t
        for w in [
            "series a",
            "series b",
            "series c",
            "funding round",
            "raised",
            "raise",
            "seed round",
            "venture",
            "strategic investment",
            "invested",
            "investment",
            "acquired",
            "acquisition",
            "merge",
            "merged",
            "merger",
        ]
    ):
        add(EventType.FUNDING_INVESTMENT_MA, confidence=0.64, impact_score=0.6)

    # Institutional adoption & strategy
    institution_words = [
        "bank",
        "asset manager",
        "asset management",
        "custody",
        "custodian",
        "corporate",
        "treasury",
    ]
    if _is_crypto_related(text) and any(w in t for w in institution_words):
        add(EventType.INSTITUTIONAL_ADOPTION_STRATEGY, confidence=0.58, impact_score=0.55)
    if ("bitcoin" in t or "btc" in t) and any(
        w in t
        for w in [
            "purchased",
            "purchase",
            "buys",
            "bought",
            "acquired",
            "added",
        ]
    ):
        add(EventType.INSTITUTIONAL_ADOPTION_STRATEGY, confidence=0.62, impact_score=0.6)

    # Market structure & liquidity shifts
    if any(
        w in t
        for w in [
            "tvl",
            "total value locked",
            "liquidity",
            "market share",
            "flows",
            "inflow",
            "outflow",
        ]
    ):
        add(EventType.MARKET_STRUCTURE_LIQUIDITY_SHIFTS, confidence=0.58, impact_score=0.5)
    if "stablecoin" in t and any(w in t for w in ["supply", "issuance", "mint", "burn"]):
        add(EventType.MARKET_STRUCTURE_LIQUIDITY_SHIFTS, confidence=0.6, impact_score=0.55)
    if "exchange" in t and any(
        w in t
        for w in [
            "product",
            "launched",
            "roll out",
            "rolled out",
            "derivatives",
            "options",
        ]
    ):
        add(EventType.MARKET_STRUCTURE_LIQUIDITY_SHIFTS, confidence=0.58, impact_score=0.5)
    if any(
        w in t
        for w in [
            "volatility",
            "sell-off",
            "selloff",
            "plunge",
            "dump",
            "rally",
            "surge",
            "crash",
        ]
    ) and _is_crypto_related(text) and any(
        w in t
        for w in [
            "btc",
            "bitcoin",
            "eth",
            "ethereum",
            "crypto market",
            "altcoin",
        ]
    ):
        add(EventType.MARKET_STRUCTURE_LIQUIDITY_SHIFTS, confidence=0.6, impact_score=0.5)

    # Protocol upgrades & network changes
    if any(
        w in t
        for w in [
            "upgrade",
            "hard fork",
            "fork",
            "consensus",
            "parameter change",
            "mainnet upgrade",
        ]
    ):
        add(EventType.PROTOCOL_UPGRADES_NETWORK_CHANGES, confidence=0.62, impact_score=0.6)
    if (
        re.search(r"\brelease\s+v?\d+(?:\.\d+)*\b", t)
        and any(w in t for w in ["protocol", "client", "node", "mainnet", "testnet"])
        and _is_crypto_related(text)
    ):
        add(EventType.PROTOCOL_UPGRADES_NETWORK_CHANGES, confidence=0.58, impact_score=0.5)
    if any(w in t for w in ["validator", "validators"]) and any(
        w in t
        for w in [
            "decline",
            "dropped",
            "fallen",
            "drop",
            "down",
            "failed",
            "failure",
            "outage",
        ]
    ):
        add(EventType.PROTOCOL_UPGRADES_NETWORK_CHANGES, confidence=0.6, impact_score=0.55)

    # New protocol / product launches (includes new stablecoins)
    if (
        any(w in t for w in ["mainnet launch", "launched mainnet", "launches mainnet"])
        and _is_crypto_related(text)
    ):
        add(EventType.NEW_PROTOCOL_PRODUCT_LAUNCHES, confidence=0.62, impact_score=0.55)
    if "stablecoin" in t and any(w in t for w in ["launch", "launched", "launches", "introduced"]):
        add(EventType.NEW_PROTOCOL_PRODUCT_LAUNCHES, confidence=0.7, impact_score=0.7)

    # Interoperability & infrastructure developments
    if any(
        w in t
        for w in [
            "bridge",
            "cross-chain",
            "cross chain",
            "messaging",
            "wallet",
            "tooling",
        ]
    ):
        add(EventType.INTEROPERABILITY_INFRA_DEVELOPMENTS, confidence=0.58, impact_score=0.5)

    # Token economics & supply events
    if any(w in t for w in ["mint", "minted", "burn", "burned", "unlock", "vesting", "emissions"]):
        add(EventType.TOKEN_ECONOMICS_SUPPLY_EVENTS, confidence=0.58, impact_score=0.5)

    # Stablecoins & monetary mechanics
    if "stablecoin" in t and any(
        w in t
        for w in [
            "depeg",
            "lost its peg",
            "reserve",
            "reserves",
            "attestation",
            "audit",
            "backing",
            "collateral",
            "redemption",
        ]
    ):
        add(EventType.STABLECOINS_MONETARY_MECHANICS, confidence=0.62, impact_score=0.6)
    if "stablecoin" in t and any(
        w in t
        for w in [
            "freeze",
            "frozen",
            "halted",
            "halt",
            "bank run",
            "run on",
            "liquidity",
            "insolvency",
            "insolvent",
        ]
    ):
        add(EventType.STABLECOINS_MONETARY_MECHANICS, confidence=0.6, impact_score=0.65)

    # Yield, rates & return dynamics
    if any(w in t for w in ["apy", "apr", "staking yield", "yield", "incentives"]):
        add(EventType.YIELD_RATES_RETURN_DYNAMICS, confidence=0.55, impact_score=0.45)
    if "staking" in t and (
        any(w in t for w in ["apy", "apr", "yield"])
        or re.search(r"\b\d+(?:\.\d+)?\s*%\b", t)
    ):
        add(EventType.YIELD_RATES_RETURN_DYNAMICS, confidence=0.56, impact_score=0.45)
    if re.search(r"\b(interest|borrow|lending|deposit|funding)\s+rate(s)?\b", t):
        add(EventType.YIELD_RATES_RETURN_DYNAMICS, confidence=0.54, impact_score=0.45)

    # RWA developments
    if any(
        w in t
        for w in [
            "tokenized bond",
            "tokenized bonds",
            "tokenized",
            "commodities",
            "real estate",
            "credit",
            "funds",
        ]
    ):
        add(EventType.RWA_DEVELOPMENTS, confidence=0.58, impact_score=0.5)

    # Payments, commerce & consumer adoption
    payment_terms = [
        "payments",
        "payment",
        "merchant",
        "commerce",
        "remittance",
        "payment rails",
        "checkout",
    ]
    payment_actions = [
        "enabled",
        "enable",
        "allowing",
        "allow",
        "pay with",
        "accept",
        "accepted",
        "payout",
        "payouts",
        "settlement",
        "transfers",
        "transfer",
    ]
    if any(term in t for term in payment_terms) and any(action in t for action in payment_actions):
        add(EventType.PAYMENTS_COMMERCE_CONSUMER_ADOPTION, confidence=0.58, impact_score=0.5)

    # Ecosystem partnerships & integrations
    if any(
        w in t
        for w in [
            "partnership",
            "partnered",
            "integration",
            "integrated",
            "collaboration",
            "alliance",
        ]
    ):
        add(EventType.ECOSYSTEM_PARTNERSHIPS_INTEGRATIONS, confidence=0.55, impact_score=0.45)

    # Company buckets
    if any(w in t for w in ["earnings", "revenue", "balance sheet", "profit", "profitability"]):
        add(EventType.COMPANY_FINANCIAL_PERFORMANCE, confidence=0.55, impact_score=0.45)
    if any(w in t for w in ["ceo", "cfo", "board", "chair", "layoffs", "restructuring"]):
        add(EventType.CORPORATE_GOVERNANCE_LEADERSHIP_CHANGES, confidence=0.55, impact_score=0.45)
    if any(w in t for w in ["pivot", "refocus", "exiting", "exit", "entering", "geographic exit"]):
        add(EventType.BUSINESS_MODEL_STRATEGIC_PIVOT, confidence=0.6, impact_score=0.55)

    return candidates


def select_primary_event(text: str) -> CandidateEvent:
    candidates = _candidates(text)
    if not candidates:
        if _is_crypto_related(text):
            return CandidateEvent(
                event_type=EventType.MISC_OTHER,
                confidence=0.45,
                impact_score=0.25,
            )
        return CandidateEvent(event_type=EventType.UNKNOWN, confidence=0.4, impact_score=0.2)

    # Highest impact wins; then confidence; then precedence; then first-mention order.
    def key(c: CandidateEvent) -> tuple[float, float, int]:
        return (c.impact_score, c.confidence, -_PRECEDENCE.get(c.event_type, 999))

    # Sort descending by impact/confidence; precedence handled by negative rank above.
    best = max(candidates, key=key)
    return best
