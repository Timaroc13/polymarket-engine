"""Polymarket informed-flow detector (wallet-flow capability, Phase 1).

Ported from the standalone polymarket_detector.py script. Reconstructs
per-wallet net USDC positions from recent Data API trades, flags "new"
wallets (first trade <=14 days ago AND exactly one market traded), and
scores each market on dominant-side new-wallet flow plus a volume burst.

Scoring and field names are kept identical to the script so historical
scan JSON stays comparable. All scoring functions are pure; network
access is isolated in the fetch_* helpers.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_URL = "https://data-api.polymarket.com"

NEW_WALLET_AGE_SECONDS = 14 * 86400
REQUEST_DELAY_SECONDS = 0.05  # pacing between calls; bump on 429s

# /trades pagination limits (observed): limit max 500, offset breaks past ~3000.
TRADES_PAGE_SIZE = 500
TRADES_MAX_OFFSET = 3000  # cumulative hard cap
MIN_TRADE_USDC = 50       # filter sub-$50 dust

# Gamma tag_ids for server-side category retrieval (verified live).
# Only crypto is mapped today; categories without a tag_id fall back to a
# volume-ranked pool classified client-side by classify_category().
CATEGORY_TAG_IDS = {"crypto": 21}
DEFAULT_CATEGORIES = ("crypto",)

# ---------- Category classification ----------

_CAT_PATTERNS = [
    ("sports", re.compile(
        r"\bvs\.?\b|\bo/u\b|over/under|moneyline|nba|nfl|mlb|nhl|ufc|\bf1\b|"
        r"grand prix|premier league|la liga|serie a|bundesliga|champions league|"
        r"world cup|\bwin on \d{4}-", re.I)),
    ("crypto", re.compile(
        r"bitcoin|btc|ethereum|\beth\b|solana|\bsol\b|\bxrp\b|dogecoin|\bdoge\b|"
        r"crypto|token|stablecoin|\bdefi\b|\bnft\b|binance|coinbase|memecoin|"
        r"altcoin|\busdc\b|\busdt\b|halving|blockchain|\bcoin\b|airdrop", re.I)),
    ("macro", re.compile(
        r"\bfed\b|federal reserve|interest rate|rate cut|rate hike|\bbps\b|"
        r"basis point|\bcpi\b|inflation|recession|jobs report|unemployment|"
        r"\bgdp\b|fomc|powell", re.I)),
    ("geopolitics", re.compile(
        r"\bwar\b|ceasefire|peace deal|invade|invasion|nuclear|treaty|sanction|"
        r"military|troops|hostage|\bcoup\b|missile|airstrike", re.I)),
    ("politics", re.compile(
        r"election|president|senate|congress|governor|nominee|primary|impeach|"
        r"\btrump\b|\bbiden\b|\bharris\b|republican|democrat|parliament|"
        r"prime minister|approval rating|ballot|\bvote\b", re.I)),
    ("tech", re.compile(
        r"\bipo\b|openai|\bgpt\b|\bapple\b|google|tesla|spacex|nvidia|ai model|"
        r"product launch|\blaunch a\b", re.I)),
    ("entertainment", re.compile(
        r"oscar|grammy|\balbum\b|box office|\bmovie\b|\bfilm\b|celebrity|netflix|"
        r"\bsong\b|\bemmy\b|award", re.I)),
]

CATEGORIES = ("crypto", "sports", "macro", "politics", "geopolitics",
              "tech", "entertainment", "other")


def classify_category(question: str, tags: list | None = None) -> str:
    """Classify a market into one fixed category from its tags + question text."""
    haystack = question or ""
    if tags:
        haystack += " " + " ".join(str(t) for t in tags)
    for name, pat in _CAT_PATTERNS:
        if pat.search(haystack):
            return name
    return "other"


# ---------- HTTP ----------

def _get(url: str, params: dict | None = None, retries: int = 3) -> Any:
    """GET with retry on 429/5xx. Returns [] on 400 (bad params, e.g. offset too deep)."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    last_exc: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "crypto-news-parser/wallet-flow"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            time.sleep(REQUEST_DELAY_SECONDS)
            return body
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                last_exc = exc
                time.sleep(2 ** attempt)
                continue
            # Don't retry 400s — they indicate bad params (e.g., offset too high).
            # Return empty so the caller can stop paginating gracefully.
            if exc.code == 400:
                return []
            last_exc = exc
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
        except Exception as exc:  # network errors, JSON decode
            last_exc = exc
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed after {retries} retries: {url}") from last_exc


# ---------- Utils ----------

def _parse_iso(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _safe_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _annotate_market(m: dict, days_to_resolution: int) -> dict:
    m["_days_to_resolution"] = days_to_resolution
    m["_liquidity_usdc"] = _safe_float(m.get("liquidity") or m.get("liquidityNum") or 0)
    m["_volume_usdc"] = _safe_float(m.get("volumeNum") or m.get("volume") or 0)
    m["_volume_1wk_usdc"] = _safe_float(m.get("volume1wk") or 0)
    m["_volume_1mo_usdc"] = _safe_float(m.get("volume1mo") or 0)
    return m


def extract_yes_price(market: dict) -> float | None:
    """YES implied probability at scan time from the Gamma market object, or None."""
    raw = market.get("outcomePrices")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if isinstance(raw, list) and raw:
        p = _safe_float(raw[0])
        if 0.0 <= p <= 1.0:
            return p
    return None


# ---------- Gamma: markets ----------

def _fetch_category_pool(category: str) -> list[dict]:
    """Fetch a candidate market pool for one category.

    Uses the Gamma tag_id when known (server-side, reliable — volume-ranking
    starves minority categories like crypto). Falls back to a volume-ranked
    pool classified client-side for categories without a tag_id.
    """
    params = {
        "active": "true",
        "closed": "false",
        "archived": "false",
        "order": "volume24hr",
        "ascending": "false",
        "limit": 100,
    }
    tag_id = CATEGORY_TAG_IDS.get(category)
    if tag_id is not None:
        params["tag_id"] = tag_id
        markets = _get(f"{GAMMA_URL}/markets", params=params)
        for m in markets:
            m["_category"] = category
        return markets
    # Fallback: general pool, keep only markets that classify to this category.
    markets = _get(f"{GAMMA_URL}/markets", params=params)
    out = []
    for m in markets:
        if classify_category(m.get("question") or "", m.get("tags")) == category:
            m["_category"] = category
            out.append(m)
    return out


def fetch_top_markets(
    top_n: int = 20,
    max_days_to_resolution: int = 30,
    min_liquidity: float = 10_000,
    categories: tuple[str, ...] | list[str] | None = None,
) -> list[dict]:
    """Fetch active, near-resolution markets in the allowed categories.

    Default category is crypto (the detector's home turf); the old
    volume-ranked all-category behaviour is intentionally gone — it was ~81%
    sports and drowned the signal.
    """
    if not categories:
        categories = DEFAULT_CATEGORIES

    seen: set[str] = set()
    pool: list[dict] = []
    for cat in categories:
        for m in _fetch_category_pool(cat):
            cid = m.get("conditionId") or m.get("condition_id")
            if cid and cid not in seen:
                seen.add(cid)
                pool.append(m)

    now = datetime.now(UTC)
    filtered: list[dict] = []
    for m in pool:
        end_date_str = m.get("endDate") or m.get("end_date")
        if not end_date_str:
            continue
        try:
            end_dt = _parse_iso(end_date_str)
        except Exception:
            continue

        days = (end_dt - now).total_seconds() / 86400
        if days < 0 or days > max_days_to_resolution:
            continue

        liq = _safe_float(m.get("liquidity") or m.get("liquidityNum") or 0)
        if liq < min_liquidity:
            continue

        filtered.append(_annotate_market(m, int(days)))
        if len(filtered) >= top_n:
            break

    return filtered


def fetch_market_by_condition_id(condition_id: str) -> dict | None:
    """Fetch a single Gamma market by condition ID, annotated like scan results."""
    data = _get(f"{GAMMA_URL}/markets", params={"condition_ids": condition_id})
    if not data:
        return None
    m = data[0] if isinstance(data, list) else data
    end_date = m.get("endDate") or m.get("end_date")
    days = 0
    if end_date:
        try:
            days = int((_parse_iso(end_date) - datetime.now(UTC)).total_seconds() / 86400)
        except Exception:
            days = 0
    return _annotate_market(m, days)


# ---------- Data API: trades ----------

def fetch_trades_for_market(condition_id: str) -> list[dict]:
    """Fetch recent trades (descending by timestamp), capped at TRADES_MAX_OFFSET."""
    all_trades: list[dict] = []
    offset = 0
    while offset < TRADES_MAX_OFFSET:
        batch = _get(f"{DATA_URL}/trades", params={
            "market": condition_id,
            "limit": TRADES_PAGE_SIZE,
            "offset": offset,
            "takerOnly": "true",
            "filterType": "CASH",
            "filterAmount": MIN_TRADE_USDC,
        })
        if not batch:
            break
        all_trades.extend(batch)
        if len(batch) < TRADES_PAGE_SIZE:
            break
        offset += TRADES_PAGE_SIZE
    return all_trades


def fetch_wallet_metadata(wallet: str, cache: dict[str, dict]) -> dict:
    """Returns {'first_trade_ts': int|None, 'markets_traded': int}.

    Optimization: we only care about "new" wallets (markets_traded == 1).
    If a wallet has traded >=2 distinct markets we bail immediately —
    exact counts for veterans are not needed in Phase 1.
    """
    if wallet in cache:
        return cache[wallet]

    trades: list[dict] = []
    offset = 0
    distinct_markets: set[str] = set()

    while offset < TRADES_MAX_OFFSET:
        batch = _get(f"{DATA_URL}/trades", params={
            "user": wallet,
            "limit": TRADES_PAGE_SIZE,
            "offset": offset,
            "takerOnly": "true",
        })
        if not batch:
            break
        trades.extend(batch)
        for t in batch:
            cid = t.get("conditionId")
            if cid:
                distinct_markets.add(cid)
        if len(distinct_markets) >= 2:
            break
        if len(batch) < TRADES_PAGE_SIZE:
            break
        offset += TRADES_PAGE_SIZE

    if not trades:
        meta = {"first_trade_ts": None, "markets_traded": 0}
    else:
        ts_list = [t["timestamp"] for t in trades if t.get("timestamp")]
        meta = {
            "first_trade_ts": min(ts_list) if ts_list else None,
            "markets_traded": len(distinct_markets),
        }

    cache[wallet] = meta
    return meta


# ---------- Position reconstruction (pure) ----------

def build_positions_from_trades(trades: list[dict]) -> list[dict]:
    """Aggregate trades into per-wallet, per-outcome net USDC positions.

    Net USDC = sum(BUY size*price) - sum(SELL size*price).
    Drops positions with net <= 0 (closed or net-short).
    Polymarket binary markets: outcomeIndex 0 = YES, 1 = NO.
    """
    agg: dict[tuple[str, int], dict] = defaultdict(
        lambda: {"net_usdc": 0.0, "first_ts": None}
    )

    for t in trades:
        wallet = t.get("proxyWallet")
        side = t.get("side")
        size = _safe_float(t.get("size"))
        price = _safe_float(t.get("price"))
        outcome_idx = t.get("outcomeIndex")
        ts = t.get("timestamp")

        if not wallet or outcome_idx is None or size <= 0 or price <= 0:
            continue

        notional = size * price
        key = (wallet, outcome_idx)

        if side == "BUY":
            agg[key]["net_usdc"] += notional
        elif side == "SELL":
            agg[key]["net_usdc"] -= notional
        else:
            continue

        if ts is not None:
            cur = agg[key]["first_ts"]
            if cur is None or ts < cur:
                agg[key]["first_ts"] = ts

    positions = []
    for (wallet, outcome_idx), data in agg.items():
        if data["net_usdc"] <= 0:
            continue
        positions.append({
            "wallet_address": wallet,
            "outcome_index": outcome_idx,
            "side": "YES" if outcome_idx == 0 else "NO",
            "usdc_size": data["net_usdc"],
            "first_trade_ts_in_market": data["first_ts"],
        })
    return positions


# ---------- Detector (pure scoring) ----------

def detect(
    market_id: str,
    market_question: str,
    days_to_resolution: int,
    total_liquidity: float,
    positions: list[dict],
    total_volume: float = 0.0,
    volume_1wk: float = 0.0,
    volume_1mo: float = 0.0,
    p_market_at_scan: float | None = None,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """Score a market for informed-trading risk from reconstructed positions.

    Positions must carry wallet metadata fields:
      wallet_first_tx_ever_ts, wallet_total_markets_traded, position_created_at_ts
    """
    # Step 1: flag new wallets
    wallet_positions: dict[str, list[dict]] = defaultdict(list)
    for p in positions:
        wallet_positions[p["wallet_address"]].append(p)

    new_wallet_entries = []
    new_usdc_by_side = {"YES": 0.0, "NO": 0.0}

    # "Age" = how long the wallet has existed on Polymarket, measured from
    # scan time back to their earliest trade ever.
    if now_ts is None:
        now_ts = int(time.time())

    for wallet, pos_list in wallet_positions.items():
        pos_list.sort(key=lambda x: x.get("position_created_at_ts") or 0)
        first = pos_list[0]

        markets_traded = int(first.get("wallet_total_markets_traded", 0))
        first_tx_ts = first.get("wallet_first_tx_ever_ts")

        if first_tx_ts is None:
            continue

        age_sec = now_ts - first_tx_ts
        age_days = int(age_sec // 86400)

        if not (markets_traded == 1 and age_sec <= NEW_WALLET_AGE_SECONDS):
            continue

        per_side = {"YES": 0.0, "NO": 0.0}
        for p in pos_list:
            per_side[p["side"]] += float(p["usdc_size"])

        for side, size in per_side.items():
            if size > 0:
                new_usdc_by_side[side] += size
                new_wallet_entries.append({
                    "wallet_address": wallet,
                    "side": side,
                    "usdc_size": round(size, 2),
                    "wallet_age_days": age_days,
                })

    # Step 2: aggregate (per-side)
    yes_u = new_usdc_by_side["YES"]
    no_u = new_usdc_by_side["NO"]
    total_new = yes_u + no_u

    count_yes = len({e["wallet_address"] for e in new_wallet_entries if e["side"] == "YES"})
    count_no = len({e["wallet_address"] for e in new_wallet_entries if e["side"] == "NO"})
    count_total = len({e["wallet_address"] for e in new_wallet_entries})

    # Informational only — not used in scoring.
    denom = total_volume if total_volume > 0 else total_liquidity
    pct = (total_new / denom) if denom > 0 else 0.0

    # Dominant side = bigger USDC side (the side we score against)
    if total_new > 0:
        if yes_u >= no_u:
            dom_side = "YES"
            dom_usdc = yes_u
            dom_count = count_yes
        else:
            dom_side = "NO"
            dom_usdc = no_u
            dom_count = count_no
        dom_count_pct = (dom_count / count_total) if count_total > 0 else 0.0
    else:
        dom_side, dom_usdc, dom_count = None, 0.0, 0
        dom_count_pct = 0.0

    # Recent activity burst: fraction of monthly volume in the last week.
    recent_burst_pct = (volume_1wk / volume_1mo) if volume_1mo > 0 else 0.0

    # Step 3: score — measured against the DOMINANT side only.
    # (Informed traders are directional; bilateral activity is likely
    # arbitrage/noise. Scoring the dominant side filters hedged patterns.)
    score = 0
    if total_new > 0:
        if dom_count >= 3:
            score += 10
        if dom_count >= 10:
            score += 10
        if dom_count >= 20:
            score += 10
        if dom_usdc >= 5_000:
            score += 15
        if dom_usdc >= 20_000:
            score += 15
        if dom_usdc >= 100_000:
            score += 10
        if recent_burst_pct >= 0.30:
            score += 10
        if recent_burst_pct >= 0.60:
            score += 10
        if dom_count_pct >= 0.60:
            score += 5
        if dom_count_pct >= 0.80:
            score += 5

    # Step 4: tier
    if score >= 70:
        tier = "HIGH"
    elif score >= 40:
        tier = "MEDIUM"
    else:
        tier = "LOW"

    # Step 5: summary
    burst_note = f", {recent_burst_pct:.0%} burst" if volume_1mo > 0 else ""
    if total_new == 0:
        summary = "No new-wallet activity detected."
    elif tier == "HIGH":
        summary = (f"{dom_count} new wallets deployed ${dom_usdc:,.0f} on {dom_side} "
                   f"(out of {count_total} total new wallets, ${total_new:,.0f}){burst_note}. "
                   f"Strong informed-trading signal; consider fading or flagging.")
    elif tier == "MEDIUM":
        summary = (f"{dom_count} new wallets placed ${dom_usdc:,.0f} on {dom_side} "
                   f"(out of {count_total} total){burst_note}. "
                   f"Watch for follow-on flow; not yet actionable alone.")
    else:
        summary = (f"Minor activity: {count_total} new wallets, ${total_new:,.0f} total. "
                   f"Below threshold.")

    new_wallet_entries.sort(key=lambda e: e["usdc_size"], reverse=True)

    return {
        "market_id": market_id,
        "market_question": market_question,
        "signal_score": score,
        "risk_tier": tier,
        "dominant_side": dom_side,
        "dominant_side_usdc": round(dom_usdc, 2),
        "dominant_side_count": dom_count,
        "dominant_side_count_pct": round(dom_count_pct, 4),
        "new_wallet_count_yes": count_yes,
        "new_wallet_count_no": count_no,
        "new_wallet_usdc_yes": round(yes_u, 2),
        "new_wallet_usdc_no": round(no_u, 2),
        "new_wallet_count_total": count_total,
        "new_wallet_total_usdc": round(total_new, 2),
        "new_wallet_pct_of_market": round(pct, 4),
        "pct_denominator": "volume" if total_volume > 0 else "liquidity",
        "recent_burst_pct": round(recent_burst_pct, 4),
        "volume_1wk_usdc": round(volume_1wk, 2),
        "volume_1mo_usdc": round(volume_1mo, 2),
        "total_market_volume_usdc": round(total_volume, 2),
        "total_market_liquidity_usdc": round(total_liquidity, 2),
        "p_market_at_scan": p_market_at_scan,
        "days_to_resolution": days_to_resolution,
        "new_wallets": new_wallet_entries,
        "summary": summary,
    }


# ---------- Orchestration ----------

def analyze_market(
    market: dict,
    wallet_cache: dict[str, dict] | None = None,
    max_wallets: int | None = None,
) -> dict:
    """Fetch trades + wallet metadata for one annotated Gamma market and score it.

    max_wallets caps how many wallets get a metadata lookup (the slow part:
    one or more Data API calls per wallet). When set, only the top-N wallets
    by total position size are looked up; the rest are treated as not-new.
    None preserves the original full-fidelity behaviour.
    """
    if wallet_cache is None:
        wallet_cache = {}

    condition_id = market.get("conditionId") or market.get("condition_id") or ""
    question = market.get("question") or market.get("title") or "(unknown)"

    trades = fetch_trades_for_market(condition_id)
    positions = build_positions_from_trades(trades)

    usdc_by_wallet: dict[str, float] = defaultdict(float)
    for p in positions:
        usdc_by_wallet[p["wallet_address"]] += float(p["usdc_size"])
    wallets = sorted(usdc_by_wallet, key=lambda w: usdc_by_wallet[w], reverse=True)
    if max_wallets is not None:
        wallets = wallets[:max_wallets]
    for w in wallets:
        fetch_wallet_metadata(w, wallet_cache)

    for p in positions:
        meta = wallet_cache.get(p["wallet_address"], {})
        p["wallet_first_tx_ever_ts"] = meta.get("first_trade_ts")
        p["wallet_total_markets_traded"] = meta.get("markets_traded", 0)
        p["position_created_at_ts"] = p["first_trade_ts_in_market"]

    result = detect(
        condition_id,
        question,
        market.get("_days_to_resolution", 0),
        market.get("_liquidity_usdc", 0.0),
        positions,
        total_volume=market.get("_volume_usdc", 0.0),
        volume_1wk=market.get("_volume_1wk_usdc", 0.0),
        volume_1mo=market.get("_volume_1mo_usdc", 0.0),
        p_market_at_scan=extract_yes_price(market),
    )
    # Category: trust the tag we fetched under; else classify from text.
    result["category"] = market.get("_category") or classify_category(
        question, market.get("tags")
    )
    return result


def run_scan(
    *,
    top_n: int = 20,
    max_days: int = 30,
    min_liquidity: float = 10_000,
    condition_id: str | None = None,
    max_wallets: int | None = None,
    categories: tuple[str, ...] | list[str] | None = None,
) -> list[dict]:
    """Run a full flow scan. Blocking — callers in async context should use a thread."""
    if condition_id:
        m = fetch_market_by_condition_id(condition_id)
        markets = [m] if m else []
    else:
        markets = fetch_top_markets(top_n, max_days, min_liquidity, categories=categories)

    wallet_cache: dict[str, dict] = {}
    results: list[dict] = []
    for m in markets:
        try:
            results.append(analyze_market(m, wallet_cache, max_wallets=max_wallets))
        except Exception as exc:
            logger.warning("wallet_flow: market analysis failed for %s: %s",
                           m.get("conditionId"), exc)
    return results
