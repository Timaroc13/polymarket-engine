## ADDED Requirements

### Requirement: Crypto-only market allow-list
The n8n workflow's "Parse & Filter Markets" Code node SHALL filter out Polymarket markets whose `question` field does not contain at least one term from a crypto keyword allow-list (case-insensitive substring match).

The allow-list SHALL contain at minimum the following seed terms:
`bitcoin`, `btc`, `ethereum`, `eth`, `crypto`, `blockchain`, `defi`, `nft`, `stablecoin`, `usdc`, `usdt`, `sol`, `solana`, `xrp`, `ripple`, `bnb`, `coinbase`, `binance`, `layer 2`, `l2`, `dao`, `token`, `altcoin`, `web3`, `on-chain`, `halving`, `memecoin`

The allow-list SHALL be defined as a named constant (`CRYPTO_KEYWORDS`) at the top of the Code node script so it can be extended without restructuring the workflow.

Markets that do not match any allow-list term SHALL be dropped before the Tavily Search node. No Tavily API call, parser signal call, AI cross-check call, or risk call SHALL be made for non-crypto markets.

#### Scenario: Crypto market passes filter
- **WHEN** a Polymarket market has `question = "Will Bitcoin exceed $120,000 by end of Q2 2026?"`
- **THEN** the question matches the keyword `bitcoin`
- **AND** the market is included in the output and proceeds to the Tavily Search node

#### Scenario: Non-crypto market is dropped
- **WHEN** a Polymarket market has `question = "Will the US Democratic Party win the 2028 Presidential election?"`
- **THEN** the question does not match any keyword in the allow-list
- **AND** the market is excluded from the output; no downstream nodes are called for it

#### Scenario: Crypto-adjacent regulatory market passes filter
- **WHEN** a Polymarket market has `question = "Will the SEC approve a spot Ethereum ETF in 2026?"`
- **THEN** the question matches the keyword `ethereum`
- **AND** the market is included and proceeds to downstream nodes

#### Scenario: Case-insensitive matching
- **WHEN** a Polymarket market has `question = "Will ETHEREUM break $5,000?"`
- **THEN** the keyword check is case-insensitive and matches `ethereum`
- **AND** the market passes the filter

### Requirement: Crypto filter applied before volume and price filters
The crypto keyword filter SHALL be applied as the first filter in the "Parse & Filter Markets" node, before the existing `MIN_VOLUME` and `MIN_P` / `MAX_P` price-range filters, so that non-crypto markets are discarded at the earliest possible point.

#### Scenario: Non-crypto market skipped before volume check
- **WHEN** a Polymarket market has a high volume (e.g., `volumeNum = 500000`) but a non-crypto question
- **THEN** the market is dropped at the keyword filter stage
- **AND** the volume check is never evaluated for that market
