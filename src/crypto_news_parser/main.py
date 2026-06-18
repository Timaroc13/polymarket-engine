from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from contextlib import asynccontextmanager, suppress
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from . import scheduler
from .dashboard import DASHBOARD_HTML, build_dashboard_data
from .fetch import (
    FetchBlockedError,
    FetchError,
    FetchTimeoutError,
    FetchTooLargeError,
    FetchUnsupportedContentTypeError,
    fetch_url_text,
)
from .llm_adapter import (
    SIGNAL_PROMPT_VERSION,
    RefinementRequest,
    get_provider_from_env,
    stable_seed,
)
from .models import (
    MAX_TEXT_LENGTH,
    ErrorEnvelope,
    ErrorObject,
    EventType,
    EventTypeV1,
    FeedbackRequest,
    FeedbackResponse,
    FlowCalibrationResponse,
    FlowMarketResult,
    FlowScanRequest,
    FlowScanResponse,
    MarketDirection,
    ParseRequest,
    ParseResponse,
    ParseUrlRequest,
    PollResolutionItem,
    PollResolutionsResponse,
    RiskRequest,
    RiskResponse,
    SignalRequest,
    SignalResponse,
    TrackMarketRequest,
    TrackMarketResponse,
)
from .parser import (
    CandidateEvent,
    compute_market_signal,
    extract_assets,
    extract_entities,
    infer_event_subtype,
    infer_sentiment,
    resolve_jurisdiction_with_meta,
    select_primary_event,
)
from .risk import validate_risk
from .storage import (
    get_deployed_capital,
    get_flow_calibration,
    get_unresolved_markets,
    mark_market_resolved,
    persistence_enabled,
    release_capital,
    reserve_capital,
    reset_deployed_capital,
    store_feedback,
    store_flow_scan,
    store_parse_run,
    track_market,
    track_market_if_new,
)
from .wallet_flow import run_scan

load_dotenv()  # Load .env into os.environ so LLM_ENABLE, GEMINI_API_KEY etc. are available

SCHEMA_VERSION = "v2"
MODEL_VERSION = os.getenv("MODEL_VERSION", "news-parser-0.1")
REQUIRED_API_KEY = os.getenv("API_KEY")


@asynccontextmanager
async def _lifespan(app_: FastAPI):
    tasks: list[asyncio.Task] = []
    if scheduler.scheduler_enabled():
        tasks = scheduler.start(scan_fn=do_flow_scan, poll_fn=do_poll_resolutions)
    app_.state.scheduler_tasks = tasks
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with suppress(asyncio.CancelledError):
                await t


app = FastAPI(title="Crypto News Parser", version=SCHEMA_VERSION, lifespan=_lifespan)


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "docs": "/docs",
    }


def get_llm_provider():
    # Separated for test monkeypatching.
    return get_provider_from_env()


async def _maybe_refine(
    req: ParseRequest,
    primary: CandidateEvent,
    assets: list[str],
    entities: list[str],
) -> tuple[CandidateEvent, list[str], list[str]]:
    provider = get_llm_provider()
    if provider is None:
        return primary, assets, entities

    if req.deterministic and not getattr(provider, "supports_determinism", False):
        return primary, assets, entities

    # Refine only when the heuristic result is low-confidence.
    low_confidence = (
        primary.event_type.value in {"UNKNOWN", "MISC_OTHER"}
    ) or (primary.confidence < 0.65)
    if not low_confidence:
        return primary, assets, entities

    request = RefinementRequest(
        text=req.text,
        heuristic_event_type=primary.event_type,
        heuristic_confidence=primary.confidence,
        heuristic_assets=tuple(assets),
        heuristic_entities=tuple(entities),
        deterministic=req.deterministic,
        seed=stable_seed(req.text) if req.deterministic else None,
    )

    refinement = await provider.refine(request)

    new_primary = primary
    if refinement.event_type is not None:
        new_primary = CandidateEvent(
            event_type=refinement.event_type,
            confidence=primary.confidence,
            impact_score=primary.impact_score,
        )

    def merge(base: list[str], extra: list[str] | None) -> list[str]:
        if not extra:
            return base
        seen: set[str] = set(base)
        merged = list(base)
        for item in extra:
            if item not in seen:
                merged.append(item)
                seen.add(item)
        return merged

    new_assets = merge(assets, refinement.assets)
    new_entities = merge(entities, refinement.entities)
    return new_primary, new_assets, new_entities


@app.middleware("http")
async def enforce_json_content_type(request: Request, call_next):
    # Enforce JSON input (PRD: 415 for unsupported media type).
    # Do this in middleware so it runs before FastAPI attempts to parse/validate the body.
    if request.method.upper() == "POST" and request.url.path in {"/parse", "/parse_url", "/signal"}:
        content_type = request.headers.get("content-type")
        if content_type is not None:
            ct = content_type.split(";", 1)[0].strip().lower()
            if ct not in {"application/json"} and not ct.endswith("+json"):
                return _error(
                    code="UNSUPPORTED_MEDIA_TYPE",
                    message="Content-Type must be application/json.",
                    status=415,
                    details={"content_type": content_type},
                )
    return await call_next(request)


def _error_payload(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    envelope = ErrorEnvelope(error=ErrorObject(code=code, message=message, details=details or {}))
    return envelope.model_dump()


def _error(
    code: str,
    message: str,
    status: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=status, content=_error_payload(code, message, details))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    # Ensure JSON-serializable: FastAPI may include raw Exception objects in ctx.
    for err in errors:
        ctx = err.get("ctx")
        if isinstance(ctx, dict) and isinstance(ctx.get("error"), Exception):
            ctx["error"] = str(ctx["error"])
        # FastAPI can include raw bytes in 'input' for non-JSON bodies.
        if isinstance(err.get("input"), (bytes, bytearray)):
            err["input"] = (err["input"][:200]).decode("utf-8", errors="replace")

    # FastAPI uses RequestValidationError for invalid JSON too; map that to 400 per PRD.
    if any(err.get("type") == "json_invalid" for err in errors):
        return _error(
            code="INVALID_JSON",
            message="Invalid JSON.",
            status=400,
            details={"errors": errors},
        )
    return _error(
        code="INVALID_REQUEST",
        message="Request validation failed.",
        status=422,
        details={"errors": errors},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    # Preserve our documented error envelope.
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return _error(code="HTTP_ERROR", message=str(exc.detail), status=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, __: Exception) -> JSONResponse:
    return _error(code="INTERNAL_ERROR", message="Internal server error.", status=500)


def _require_api_key(authorization: str | None) -> None:
    if not REQUIRED_API_KEY:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail=_error_payload("UNAUTHORIZED", "Missing API key."),
        )
    token = authorization.split(" ", 1)[1].strip()
    if token != REQUIRED_API_KEY:
        raise HTTPException(status_code=403, detail=_error_payload("FORBIDDEN", "Invalid API key."))


@app.post("/parse", response_model=ParseResponse)
async def parse(
    req: ParseRequest,
    authorization: str | None = Header(default=None),
    response: Response = None,
) -> ParseResponse:
    _require_api_key(authorization)

    # Enforce max length here too (validator covers most cases; this makes it explicit).
    if len(req.text) > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=413,
            detail=_error_payload(
                code="PAYLOAD_TOO_LARGE",
                message=f"text exceeds max length {MAX_TEXT_LENGTH}",
                details={"max_length": MAX_TEXT_LENGTH},
            ),
        )

    primary = select_primary_event(req.text)
    assets = extract_assets(req.text)
    entities = extract_entities(req.text)
    jurisdiction, jurisdiction_basis, jurisdiction_confidence = resolve_jurisdiction_with_meta(
        req.text
    )
    sentiment = infer_sentiment(req.text)

    primary, assets, entities = await _maybe_refine(req, primary, assets, entities)

    event_subtype = infer_event_subtype(req.text, primary.event_type)

    def infer_v1_event_type(text: str, event_type: EventType) -> EventTypeV1:
        t = text.lower()
        if (
            event_type
            in {
                EventType.NEW_PROTOCOL_PRODUCT_LAUNCHES,
                EventType.STABLECOINS_MONETARY_MECHANICS,
            }
            and "stablecoin" in t
        ):
            return EventTypeV1.STABLECOIN_ISSUANCE
        if event_type == EventType.REGULATORY_ACTION_ENFORCEMENT and any(
            w in t
            for w in [
                "lawsuit",
                "sues",
                "sued",
                "charges",
                "charged",
                "indict",
                "investigation",
                "probe",
            ]
        ) and any(r in t for r in ["sec", "cftc", "doj", "fca", "esma", "ofac"]):
            return EventTypeV1.ENFORCEMENT_ACTION
        if event_type == EventType.PROTOCOL_UPGRADES_NETWORK_CHANGES:
            return EventTypeV1.PROTOCOL_UPGRADE
        if event_type == EventType.SECURITY_INCIDENTS_EXPLOITS and any(
            w in t
            for w in [
                "exchange",
                "cex",
                "bybit",
                "coinbase",
                "binance",
                "kraken",
            ]
        ):
            return EventTypeV1.EXCHANGE_HACK
        if event_type in {EventType.UNKNOWN, EventType.MISC_OTHER}:
            return EventTypeV1.UNKNOWN
        return EventTypeV1.UNKNOWN

    # Topics are intentionally loose.
    topics: list[str] = []
    if primary.event_type in {
        EventType.REGULATORY_ACTION_ENFORCEMENT,
        EventType.LEGISLATION_POLICY_DEVELOPMENT,
        EventType.GOVERNMENT_CENTRAL_BANK_INITIATIVES,
    }:
        topics = ["REGULATION"]
    elif primary.event_type == EventType.STABLECOINS_MONETARY_MECHANICS:
        topics = ["STABLECOIN"]
    elif primary.event_type == EventType.NEW_PROTOCOL_PRODUCT_LAUNCHES:
        topics = ["LAUNCH"]
    elif primary.event_type == EventType.CAPITAL_MARKETS_ACTIVITY:
        topics = ["CAPITAL_MARKETS"]
    elif primary.event_type in {
        EventType.FUNDING_INVESTMENT_MA,
        EventType.INSTITUTIONAL_ADOPTION_STRATEGY,
    }:
        topics = ["INSTITUTIONS"]
    elif primary.event_type == EventType.MARKET_STRUCTURE_LIQUIDITY_SHIFTS:
        topics = ["MARKET_STRUCTURE"]
    elif primary.event_type == EventType.SECURITY_INCIDENTS_EXPLOITS:
        topics = ["SECURITY"]
    elif primary.event_type == EventType.INTEROPERABILITY_INFRA_DEVELOPMENTS:
        topics = ["INFRA"]
    elif primary.event_type == EventType.RWA_DEVELOPMENTS:
        topics = ["RWA"]
    elif primary.event_type == EventType.PAYMENTS_COMMERCE_CONSUMER_ADOPTION:
        topics = ["PAYMENTS"]

    parsed = ParseResponse(
        event_type=primary.event_type,
        v1_event_type=infer_v1_event_type(req.text, primary.event_type),
        event_subtype=event_subtype,
        topics=topics,
        assets=assets,
        entities=entities,
        jurisdiction=jurisdiction,
        jurisdiction_basis=jurisdiction_basis,
        jurisdiction_confidence=jurisdiction_confidence,
        sentiment=sentiment,
        impact_score=primary.impact_score,
        confidence=primary.confidence,
        schema_version=SCHEMA_VERSION,
        model_version=MODEL_VERSION,
    )

    if persistence_enabled():
        stored = store_parse_run(
            input_id=req.input_id,
            source_url=req.source_url,
            source_name=req.source_name,
            source_published_at=req.source_published_at,
            text=req.text,
            response=parsed.model_dump(),
        )
        if response is not None:
            response.headers["X-Parse-Id"] = str(stored.parse_id)

    return parsed


@app.post("/feedback", response_model=FeedbackResponse)
async def feedback(
    req: FeedbackRequest,
    authorization: str | None = Header(default=None),
) -> FeedbackResponse:
    _require_api_key(authorization)

    if not persistence_enabled():
        raise HTTPException(
            status_code=400,
            detail=_error_payload(
                "PERSISTENCE_DISABLED",
                "Feedback is unavailable because persistence is disabled.",
            ),
        )

    if req.parse_id is None and not req.input_id:
        raise HTTPException(
            status_code=422,
            detail=_error_payload(
                "INVALID_REQUEST",
                "Provide either parse_id or input_id.",
                details={"fields": ["parse_id", "input_id"]},
            ),
        )

    try:
        fid = store_feedback(
            parse_id=req.parse_id,
            input_id=req.input_id,
            text=req.text,
            expected=req.expected,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=_error_payload("INVALID_REQUEST", str(e)),
        ) from e

    return FeedbackResponse(feedback_id=fid)


@app.post("/parse_url", response_model=ParseResponse)
async def parse_url(
    req: ParseUrlRequest,
    authorization: str | None = Header(default=None),
    response: Response = None,
) -> ParseResponse:
    _require_api_key(authorization)

    try:
        fetched = await fetch_url_text(req.url)
    except FetchBlockedError as e:
        raise HTTPException(
            status_code=400,
            detail=_error_payload("URL_BLOCKED", str(e), details={"url": req.url}),
        ) from e
    except FetchTooLargeError as e:
        raise HTTPException(
            status_code=413,
            detail=_error_payload("FETCH_TOO_LARGE", str(e), details={"url": req.url}),
        ) from e
    except FetchTimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail=_error_payload("FETCH_TIMEOUT", str(e), details={"url": req.url}),
        ) from e
    except FetchUnsupportedContentTypeError as e:
        raise HTTPException(
            status_code=415,
            detail=_error_payload(
                "UNSUPPORTED_FETCH_CONTENT_TYPE", str(e), details={"url": req.url}
            ),
        ) from e
    except FetchError as e:
        raise HTTPException(
            status_code=502,
            detail=_error_payload("FETCH_FAILED", str(e), details={"url": req.url}),
        ) from e

    # Reuse the main parse pipeline using extracted text.
    try:
        parse_req = ParseRequest(
            text=fetched.text,
            deterministic=req.deterministic,
            input_id=req.input_id,
            source_url=fetched.url,
            source_name=req.source_name,
            source_published_at=req.source_published_at,
        )
    except ValidationError as e:
        errors = e.errors()
        for err in errors:
            ctx = err.get("ctx")
            if isinstance(ctx, dict) and isinstance(ctx.get("error"), Exception):
                ctx["error"] = str(ctx["error"])
            if isinstance(err.get("input"), (bytes, bytearray)):
                err["input"] = (err["input"][:200]).decode("utf-8", errors="replace")
        raise HTTPException(
            status_code=422,
            detail=_error_payload(
                "INVALID_REQUEST",
                "Fetched content could not be converted into a valid parse request.",
                details={"url": req.url, "errors": errors},
            ),
        ) from e
    return await parse(parse_req, authorization=authorization, response=response)


@app.post("/signal", response_model=SignalResponse)
async def signal(
    req: SignalRequest,
    authorization: str | None = Header(default=None),
) -> SignalResponse:
    _require_api_key(authorization)

    if len(req.text) > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=413,
            detail=_error_payload(
                code="PAYLOAD_TOO_LARGE",
                message=f"text exceeds max length {MAX_TEXT_LENGTH}",
                details={"max_length": MAX_TEXT_LENGTH},
            ),
        )

    primary = select_primary_event(req.text)
    assets = extract_assets(req.text)
    entities = extract_entities(req.text)
    jurisdiction, _, _ = resolve_jurisdiction_with_meta(req.text)
    sentiment = infer_sentiment(req.text)

    primary, assets, entities = await _maybe_refine(
        ParseRequest(text=req.text, deterministic=req.deterministic),
        primary,
        assets,
        entities,
    )

    p_model_method = "heuristic"
    signal_prompt_version: str | None = None
    p_model, market_direction = compute_market_signal(
        sentiment, primary.impact_score, primary.confidence
    )

    llm_provider = get_llm_provider()
    if llm_provider is not None and req.market_question is not None:
        llm_p = await llm_provider.estimate_p_model(req.market_question, req.text)
        if llm_p is not None:
            p_model = llm_p
            if p_model > 0.55:
                market_direction = MarketDirection.bullish
            elif p_model < 0.45:
                market_direction = MarketDirection.bearish
            else:
                market_direction = MarketDirection.neutral
            p_model_method = "llm"
            signal_prompt_version = SIGNAL_PROMPT_VERSION

    return SignalResponse(
        p_model=round(p_model, 4),
        p_model_method=p_model_method,
        signal_prompt_version=signal_prompt_version,
        market_direction=market_direction,
        event_type=primary.event_type,
        sentiment=sentiment,
        impact_score=primary.impact_score,
        confidence=primary.confidence,
        assets=assets,
        jurisdiction=jurisdiction,
        schema_version=SCHEMA_VERSION,
        model_version=MODEL_VERSION,
    )


@app.post("/risk", response_model=RiskResponse)
async def risk(
    req: RiskRequest,
    authorization: str | None = Header(default=None),
) -> RiskResponse:
    _require_api_key(authorization)
    if req.auto_reserve and persistence_enabled():
        # Replace caller-supplied deployed with server-side atomic value
        current_deployed = get_deployed_capital()
        req = req.model_copy(update={"deployed": current_deployed})
    result = validate_risk(req)
    if (
        req.auto_reserve
        and persistence_enabled()
        and result.verdict == "GO"
        and result.bet_size is not None
    ):
        reserve_capital(result.bet_size, bet_id=req.bet_id)
    return result


@app.get("/deployed")
async def get_deployed(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return the current server-side deployed capital."""
    _require_api_key(authorization)
    if not persistence_enabled():
        return {"deployed": 0.0, "note": "persistence disabled — use manual deployed tracking"}
    return {"deployed": round(get_deployed_capital(), 2)}


@app.post("/deployed/release")
async def release_deployed(
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Release (subtract) capital when a bet is settled or cancelled."""
    _require_api_key(authorization)
    if not persistence_enabled():
        return {"deployed": 0.0, "note": "persistence disabled"}
    amount = float(body.get("amount", 0.0))
    new_total = release_capital(amount)
    return {"deployed": round(new_total, 2)}


@app.post("/deployed/reset")
async def reset_deployed(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Reset deployed capital to zero (start of new trading session)."""
    _require_api_key(authorization)
    if not persistence_enabled():
        return {"deployed": 0.0, "note": "persistence disabled"}
    reset_deployed_capital()
    return {"deployed": 0.0}


async def do_flow_scan(req: FlowScanRequest) -> FlowScanResponse:
    """Run a flow scan and persist/track results. Shared by the route and the scheduler."""
    results = await asyncio.to_thread(
        run_scan,
        top_n=req.top_n,
        max_days=req.max_days,
        min_liquidity=req.min_liquidity,
        condition_id=req.condition_id,
        max_wallets=req.max_wallets,
    )

    stored = False
    if persistence_enabled():
        for r in results:
            store_flow_scan(result=r)
            if r.get("market_id"):
                track_market_if_new(
                    condition_id=str(r["market_id"]),
                    question=r.get("market_question"),
                )
        stored = True

    return FlowScanResponse(
        results=[FlowMarketResult(**r) for r in results],
        scanned=len(results),
        stored=stored,
    )


@app.post("/flow-scan", response_model=FlowScanResponse)
async def flow_scan(
    req: FlowScanRequest,
    authorization: str | None = Header(default=None),
) -> FlowScanResponse:
    """Scan Polymarket markets for informed-trading flow (new-wallet detector).

    Blocking external API calls run in a worker thread; a 20-market scan can
    take minutes. Intended for the in-app scheduler or manual/scheduled calls.
    """
    _require_api_key(authorization)
    return await do_flow_scan(req)


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page() -> HTMLResponse:
    """Localhost KPI dashboard (read-only, unauthenticated by design)."""
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/dashboard/data", include_in_schema=False)
async def dashboard_data() -> dict[str, Any]:
    """JSON payload behind /dashboard (read-only, unauthenticated by design)."""
    if not persistence_enabled():
        raise HTTPException(
            status_code=400,
            detail=_error_payload(
                "PERSISTENCE_DISABLED",
                "Dashboard data is unavailable because persistence is disabled.",
            ),
        )
    return build_dashboard_data()


@app.get("/flow-calibration", response_model=FlowCalibrationResponse)
async def flow_calibration(
    authorization: str | None = Header(default=None),
) -> FlowCalibrationResponse:
    """Report flow-scan calibration: dominant-side win rate vs. implied probability."""
    _require_api_key(authorization)

    if not persistence_enabled():
        raise HTTPException(
            status_code=400,
            detail=_error_payload(
                "PERSISTENCE_DISABLED",
                "Flow calibration is unavailable because persistence is disabled.",
            ),
        )

    return FlowCalibrationResponse(**get_flow_calibration())


_POLYMARKET_CLOB_URL = "https://clob.polymarket.com/markets/{condition_id}"


def _fetch_polymarket_market(condition_id: str) -> dict[str, Any] | None:
    """Fetch market data from Polymarket CLOB API. Returns the parsed JSON or None on error.

    A User-Agent header is required — without it the CLOB API returns 403.
    """
    url = _POLYMARKET_CLOB_URL.format(condition_id=condition_id)
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "polymarket-engine/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logging.getLogger(__name__).warning("Polymarket fetch failed for %s: %s", condition_id, exc)
        return None


def _extract_resolution(data: dict[str, Any]) -> str | None:
    """Return the resolved outcome for a CLOB market, or None if not yet settled.

    The CLOB market records resolution on the `tokens` array: the winning
    outcome has `winner == True` (price ~1). We map the winning token's
    *index* to the same YES(0)/NO(1) convention used by `dominant_side` and
    `p_market_at_scan`, so calibration and paper-trading count it. For
    non-binary markets (BL-07) the raw winning label is returned, which
    calibration excludes from the YES/NO math.
    """
    if not data.get("closed"):
        return None
    tokens = data.get("tokens")
    if not isinstance(tokens, list) or not tokens:
        return None

    win_idx: int | None = None
    win_label: str | None = None
    for i, t in enumerate(tokens):
        if isinstance(t, dict) and t.get("winner") is True:
            win_idx, win_label = i, t.get("outcome")
            break
    if win_idx is None:  # fallback: a fully-priced token marks the winner
        for i, t in enumerate(tokens):
            try:
                if isinstance(t, dict) and float(t.get("price") or 0) >= 0.99:
                    win_idx, win_label = i, t.get("outcome")
                    break
            except (TypeError, ValueError):
                continue
    if win_idx is None:
        return None  # closed but not yet settled — keep polling

    if len(tokens) == 2:
        return "Yes" if win_idx == 0 else "No"
    return str(win_label) if win_label else None


@app.post("/track-market", response_model=TrackMarketResponse)
async def track_market_endpoint(
    req: TrackMarketRequest,
    authorization: str | None = Header(default=None),
) -> TrackMarketResponse:
    """Register a Polymarket market for resolution tracking."""
    _require_api_key(authorization)

    if not persistence_enabled():
        raise HTTPException(
            status_code=400,
            detail=_error_payload(
                "PERSISTENCE_DISABLED",
                "Market tracking is unavailable because persistence is disabled.",
            ),
        )

    try:
        market_id = track_market(
            condition_id=req.condition_id,
            question=req.question,
            parse_id=req.parse_id,
            input_id=req.input_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_payload("INVALID_REQUEST", str(exc)),
        ) from exc

    return TrackMarketResponse(market_id=market_id, condition_id=req.condition_id)


async def do_poll_resolutions() -> PollResolutionsResponse:
    """Poll Polymarket for resolved tracked markets. Shared by the route and the scheduler."""
    markets = get_unresolved_markets()
    resolved_items: list[PollResolutionItem] = []
    errors: list[str] = []

    for market in markets:
        data = _fetch_polymarket_market(market.condition_id)
        if data is None:
            errors.append(f"{market.condition_id}: failed to fetch from Polymarket")
            continue

        # CLOB marks resolution on the tokens array; map the winner to YES/NO.
        outcome = _extract_resolution(data)
        if not outcome:
            continue

        # Auto-POST to /feedback
        try:
            fid = store_feedback(
                parse_id=market.parse_id,
                input_id=market.input_id,
                text=None,
                expected={"outcome": outcome, "condition_id": market.condition_id},
                notes=f"Auto-resolved via poll-resolutions: outcome={outcome}",
            )
            mark_market_resolved(condition_id=market.condition_id, outcome=str(outcome))
            resolved_items.append(
                PollResolutionItem(
                    condition_id=market.condition_id,
                    outcome=str(outcome),
                    feedback_id=fid,
                )
            )
        except Exception as exc:
            errors.append(f"{market.condition_id}: feedback storage failed: {exc}")

    return PollResolutionsResponse(
        resolved=resolved_items,
        checked=len(markets),
        errors=errors,
    )


@app.post("/poll-resolutions", response_model=PollResolutionsResponse)
async def poll_resolutions(
    authorization: str | None = Header(default=None),
) -> PollResolutionsResponse:
    """Check Polymarket for resolved markets and auto-POST /feedback for each resolution.

    Queries the local DB for all tracked markets that haven't been resolved yet,
    calls the Polymarket CLOB API for each, and if resolved, stores feedback and
    marks the market as resolved.

    Called by the in-app scheduler (SCHEDULER_ENABLE=1) or manually.
    """
    _require_api_key(authorization)

    if not persistence_enabled():
        raise HTTPException(
            status_code=400,
            detail=_error_payload(
                "PERSISTENCE_DISABLED",
                "Resolution polling is unavailable because persistence is disabled.",
            ),
        )

    return await do_poll_resolutions()
