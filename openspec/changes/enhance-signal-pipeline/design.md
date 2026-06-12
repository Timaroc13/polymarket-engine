# Design: Enhanced Signal Pipeline

## Context

The predict-market-risk pipeline is an n8n workflow that runs every 15 minutes:

```
Schedule → Fetch Polymarket (top 30 by volume) → Parse & Filter Markets
  → Tavily Search → Build Signal Input → POST /signal
  → Merge Signal → Edge Filter (≥4%) → POST /risk
  → Merge Risk → Verdict Filter (GO) → Dedup (24h)
  → Format Alert → Telegram
```

Three problems are in scope:

1. No feedback path: once a market resolves the pipeline forgets whether `p_model` was right.
2. No independent check on `p_model`: the heuristic formula (`sentiment × impact × confidence`) does not interpret the specific market question, so it can be directionally correct for the news but orthogonal to the binary outcome being priced.
3. No domain filter: political, sports, and entertainment markets are passed through, wasting Tavily quota and producing noise alerts where the parser's taxonomy produces meaningless `event_type` / `p_model` values.

## Goals / Non-Goals

**Goals**
- Record resolved outcomes against the `p_model` that was sent, creating a calibration dataset
- Detect when the heuristic `p_model` and an AI estimate disagree materially, and suppress or flag the alert accordingly
- Drop non-crypto markets before any expensive downstream call
- Keep all three changes as small, isolated additions to the existing n8n workflow and the existing feedback API contract

**Non-Goals**
- Runtime retuning of the `p_model` formula from feedback (batch / offline calibration is out of scope)
- Polymarket resolution event streaming or webhook setup (the spec documents what to submit, not how to detect resolution)
- Changing the parser API schema or Python implementation
- Replacing the heuristic `p_model` with the AI estimate (AI is a cross-check, not the primary signal)

## Decisions

### Decision 1: Feedback via existing `POST /feedback` — no new endpoint

The parser already exposes `POST /feedback` and accepts a free-form `expected` dict. We extend the convention by specifying that the n8n workflow SHOULD submit a feedback record when a market resolves, using the `expected.p_model_resolved` key to carry the binary outcome (`1.0` = YES, `0.0` = NO) and `expected.p_model_at_alert` to carry the heuristic estimate that generated the alert.

**Rationale**: No API changes required. The feedback store already supports this. Offline calibration scripts can query the store for records that have `expected.p_model_resolved` set and compute calibration metrics.

**Alternative considered**: Add a dedicated `/calibration` endpoint on the parser. Rejected — adds surface area without benefit until we have a calibration pipeline that reads from it.

### Decision 2: AI cross-check runs after "Merge Signal", before "Edge Filter"

Placement rationale:
- After "Merge Signal": `p_model` and all supporting signals are available as input to the Claude prompt.
- Before "Edge Filter": if AI and heuristic diverge materially the market is dropped before the 4% edge check, saving the `/risk` call.

The node sends a structured prompt containing:
- The article text (truncated to 1 500 chars to stay within prompt budget)
- The `market_question`
- The heuristic `p_model` from the parser

Claude responds with a JSON object `{ "p_ai": float, "reasoning": string }`.

The workflow Code node that follows computes `divergence = |p_model - p_ai|` and sets `ai_verdict` to `AGREE` (divergence ≤ threshold, default 0.15) or `DIVERGE`.

**Rationale for threshold 0.15**: Matches the "material disagreement" band used informally in the edge filter. A 15-point gap between an LLM estimate and the heuristic is a strong signal of formula mis-application.

**Alternative considered**: Use Claude to replace `p_model` entirely. Rejected — the heuristic formula is fast, cheap, deterministic, and already calibrated to the taxonomy. AI adds a second opinion, not a replacement.

### Decision 3: Crypto keyword allow-list applied in the existing "Parse & Filter Markets" node

The allow-list is a set of case-insensitive substrings applied to the `question` field. A market passes if its question contains at least one keyword from the list.

Seed list (non-exhaustive, extendable):
`bitcoin`, `btc`, `ethereum`, `eth`, `crypto`, `blockchain`, `defi`, `nft`, `stablecoin`, `usdc`, `usdt`, `sol`, `solana`, `xrp`, `ripple`, `bnb`, `coinbase`, `binance`, `polymarket crypto`, `layer 2`, `l2`, `dao`, `token`, `altcoin`, `web3`, `on-chain`, `halving`, `memecoin`

**Rationale**: Applying the filter in the existing Code node keeps the workflow node count unchanged. The allow-list is embedded as a constant at the top of the node's JS code, making it easy to extend without structural changes.

**Alternative considered**: A separate n8n IF node with condition on `question`. Rejected — adds a node, splits the output stream, and is harder to maintain than a list in code.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| AI cross-check adds latency (Claude API round trip ~1–3s per market) | The cross-check runs per-item after Edge Filter drops most markets; expected throughput is low (1–5 GO candidates per cycle) |
| Keyword allow-list is too narrow — misses valid crypto markets | List is conservative by design; false negatives are safer than false positives (non-crypto noise). Extend iteratively using missed-market logs |
| Keyword allow-list is too broad — lets through hybrid markets (e.g., "Will a crypto ETF get US approval?") | These are acceptable: the parser handles regulatory events well and the market question is crypto-adjacent |
| Claude prompt returns malformed JSON | n8n Code node wraps the Claude response in a try/catch; on parse error `p_ai` defaults to `p_model` and `ai_verdict` is set to `PARSE_ERROR` (treated as AGREE to avoid false suppression) |
| Feedback records accumulate without being consumed | Feedback store already has no TTL enforcement; this is an operational concern documented as an open question |

## Open Questions

- Should `DIVERGE` markets be sent as a lower-priority Telegram alert (flagged) rather than silently dropped? (Current spec: dropped. Revisit if users want visibility.)
- What is the target calibration cycle — weekly batch? On-demand script? (Out of scope for this change; to be addressed in a `calibrate-signal-formula` change.)
- Should the allow-list be externalised to an n8n variable (`$vars.CRYPTO_KEYWORDS`) so it can be updated without re-importing the workflow JSON?
