# Tasks: add-trade-ledger

> Blocked on proposal approval. Resolve the maker-fill double-counting question (proposal §Impact) in design.md first.

## 1. Storage

- [ ] 1.1 `trades` table: tx_hash, event_index, condition_id, proxy_wallet, side, outcome_index, size, price, timestamp, is_maker; UNIQUE(tx_hash, event_index, proxy_wallet)
- [ ] 1.2 `ledger_markets` cursor table: condition_id PK, last_trade_ts, last_synced_at, trade_count
- [ ] 1.3 Insert-or-ignore bulk writer + per-market position-reconstruction query (net USDC per wallet/outcome)

## 2. Ingestion

- [ ] 2.1 `sync_market_trades(condition_id)`: page Data API forward from last_trade_ts with `takerOnly=false`; stop at cap or overlap
- [ ] 2.2 `POST /ledger-sync`: sync all unresolved tracked markets; return per-market new-trade counts; API-key + persistence guards

## 3. Wallet-flow integration

- [ ] 3.1 `/flow-scan` position source: ledger when covered, live snapshot otherwise; result gains `position_source` field
- [ ] 3.2 n8n: add ledger-sync HTTP node before Flow Scan in the 4-hour workflow

## 4. Tests & validation

- [ ] 4.1 Dedupe/idempotency tests; cursor advancement; maker+taker same-fill handling per design decision
- [ ] 4.2 Ledger-vs-snapshot reconstruction equivalence on identical trade sets
- [ ] 4.3 `ruff check`, full pytest, `openspec validate add-trade-ledger --strict --no-interactive`
