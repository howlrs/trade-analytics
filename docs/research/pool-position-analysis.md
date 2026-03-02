# Cetus CLMM USDC/SUI Pool Position Analysis

## Pool Overview

| Metric | Value |
|---|---|
| Pool ID | `0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105` |
| Pool Type | CLMM (Concentrated Liquidity) |
| Token Pair | USDC (coinA) / SUI (coinB) |
| Fee Tier | 0.25% (fee_rate: 2500) |
| Tick Spacing | 60 |
| Created | October 8, 2024 |
| Active Positions | 9,959 |
| Current Tick | 69,761 |
| Pool Status | Active (is_pause: false) |

### Token Balances (On-Chain, Feb 19 2026)

| Token | Balance | USD Value |
|---|---|---|
| USDC | 1,201,226 | $1,201,226 |
| SUI | 4,151,736 | $3,878,552 |
| **Total TVL** | | **$5,079,778** |

### Current Price

- SUI per USDC (raw CLMM direction): 1.0704
- **USDC per SUI: $0.934** (1/1.0704)
- Source: on-chain `current_sqrt_price` converted via `sqrtPriceX64ToPrice`

### Reward Emissions

The pool distributes two reward tokens:
1. **CETUS** - ~0.427 tokens/sec
2. **SUI** - ~0.485 tokens/sec (native SUI rewards)

These emissions provide additional yield on top of trading fees, significantly boosting APR for in-range positions.

---

## Volume and Fee Analysis

### Daily Trading Volume

| Source | 24h Volume | 24h Transactions |
|---|---|---|
| GeckoTerminal | $4.99M | 2,598 |
| DexPaprika | $2.80M | 2,936 |
| **Average estimate** | **~$3.9M** | **~2,750** |

Volume variation is normal across data sources due to different measurement windows.

### Fee Revenue Estimates

| Metric | Value |
|---|---|
| Fee rate | 0.25% |
| Daily fees (at $3.9M vol) | ~$9,738 |
| Annual fees | ~$3.55M |
| **Base Fee APR (full pool TVL)** | **~70%** |

This is the base APR if liquidity were spread across the entire price range. Concentrated positions earn multiples of this based on range width.

---

## Tick Spacing and Range Width Analysis

With tick spacing = 60, each tick step represents a ~0.60% price change. Key range width mappings:

| Tick Steps | Total Ticks | Price Range | From Center |
|---|---|---|---|
| 1 | 60 | 0.6% | +/- 0.3% |
| 2 | 120 | 1.2% | +/- 0.6% |
| 4 | 240 | 2.4% | +/- 1.2% |
| 5 | 300 | 3.0% | +/- 1.5% |
| 8 | 480 | 4.9% | +/- 2.5% |
| 10 | 600 | 6.2% | +/- 3.1% |
| 15 | 900 | 9.4% | +/- 4.7% |
| 20 | 1200 | 12.7% | +/- 6.4% |

### Capital Efficiency by Range Width

| Range Width | Capital Efficiency | Effective APR (at $3.9M daily vol) |
|---|---|---|
| +/- 3% | ~33.8x | ~2,367% |
| +/- 5% | ~20.5x | ~1,434% |
| +/- 10% | ~10.5x | ~733% |
| +/- 15% | ~7.1x | ~499% |
| +/- 20% | ~5.4x | ~381% |

**Caveat**: These theoretical APRs assume 100% time-in-range and proportional fee share. Real yields are lower due to:
- Time out of range (earning zero fees)
- Competition from other concentrated LPs in the same ticks
- Rebalancing costs
- Impermanent loss

---

## Position Distribution Analysis

### Pool-Level Statistics

The pool has **9,959 positions** tracked in the position manager. This is a highly active pool with significant LP participation.

For a pool with ~$5M TVL and ~10K positions:
- **Average position size**: ~$510
- This suggests many small retail LPs alongside fewer large positions

### Estimated Distribution (Based on CLMM Research)

Based on Uniswap v3 research (comparable CLMM mechanics) and Cetus pool structure:

| Category | Est. Position Count | Est. TVL Share | Typical Range Width |
|---|---|---|---|
| Micro (<$100) | ~5,000-6,000 | ~5-10% | Variable, often full-range |
| Small ($100-$1K) | ~2,500-3,000 | ~15-20% | Wide (+/- 10-20%) |
| Medium ($1K-$5K) | ~800-1,200 | ~20-30% | Moderate (+/- 5-10%) |
| Large ($5K-$50K) | ~200-400 | ~25-35% | Narrow-to-moderate (+/- 3-8%) |
| Whale ($50K+) | ~20-50 | ~15-25% | Strategic, often narrow |

### Our Position Category

At **$3,000**, our bot sits in the **medium** category. This is a sweet spot where:
- Position is large enough to justify active management costs
- Gas costs are negligible relative to position size
- Swap fees (0.25%) on rebalance are meaningful but manageable
- Competition from automated vaults (Cetus Vaults, Kriya) is moderate

---

## Rebalance Cost Analysis for $3,000 Position

### Cost Breakdown

A full rebalance involves: close position -> swap tokens -> open new position.

| Cost Component | Amount |
|---|---|
| Gas (3 TXs) | ~$0.009 |
| Swap fee (50% of $3K) | ~$3.75 |
| **Total rebalance cost** | **~$3.76** |

Gas is negligible on Sui. The **swap fee (0.25%) dominates** at ~99.8% of total cost.

### Break-Even Analysis

How quickly does fee income recover rebalance costs?

| Range Width | Daily Fee Income | Break-Even Time |
|---|---|---|
| +/- 3% | $194.52 | ~0.5 hours |
| +/- 5% | $117.82 | ~0.8 hours |
| +/- 10% | $60.24 | ~1.5 hours |
| +/- 15% | $41.00 | ~2.2 hours |

**Break-even is very fast** for all range widths at current volume levels. This validates aggressive rebalancing for a $3K position.

However, these are theoretical maximums assuming proportional fee share. With 9,959 positions competing for fees, actual share depends on the liquidity density around the current price.

---

## Competitive Landscape: Automated Vault Strategies

### Kamino (Solana, reference architecture)
- Rebalance check interval: 20 minutes
- Uses reference price + max slippage threshold
- Strategy-specific: stable pairs rarely rebalance, volatile pairs more actively
- Waits for price stability before completing rebalance (avoids rebalancing during spikes)

### Kriya (Sui)
- "Target LP Range" (positioning) + "Reset Range" (rebalance trigger)
- Auto-compounds fees + reward emissions into position
- Converts all yield to SUI before reinvesting

### Cetus Vaults
- Native vault product from Cetus
- Automated range management
- Generally wider ranges for stability

### Key Takeaway for Our Bot

Our bot competes with these automated solutions. Advantages of self-managed:
- Custom strategy parameters tuned to our risk tolerance
- No vault management fees (typically 2-10% of yield)
- Direct control over rebalance timing
- Can incorporate external signals (volatility, volume trends)

---

## Cetus Protocol Risk Assessment

### Hack History (May 22, 2025)
- $223M+ exploit via arithmetic overflow in CLMM contracts
- Platform relaunched June 8, 2025
- ~50% of pre-hack TVL recovered (~$120M)
- Remaining LP losses compensated via CETUS token vesting (12 months, through June 2026)
- $30M USDC loan from Sui Foundation used for recovery
- Contract vulnerabilities patched; third-party audits completed

### Current Risk Level (Feb 2026)
- Protocol is operational and recovering
- Smart contract risk remains elevated (post-exploit)
- Pool TVL ($5M) is significantly lower than pre-hack levels
- Compensation program still ongoing

---

## Recommendations for $3,000 LP Positioning

### Optimal Parameters

| Parameter | Recommended Value | Rationale |
|---|---|---|
| **Range width** | 240-600 ticks (+/-1.2% to +/-3.1%) | Balance fee capture vs rebalance frequency |
| **Strategy** | `dynamic` (volatility-based) | Adapts to market conditions |
| **volTickWidthMin** | 240 | Minimum 2.4% range in calm markets |
| **volTickWidthMax** | 600 | Up to 6.2% range in volatile markets |
| **Rebalance threshold** | 0.10-0.15 | Trigger at 10-15% into edge of range |
| **Check interval** | 30 seconds | Frequent enough for fast-moving SUI |
| **Compound interval** | 3600-7200 sec | Compound fees every 1-2 hours |
| **Slippage tolerance** | 0.01 (1%) | Tight enough for USDC/SUI liquidity |

### Strategic Considerations

1. **Narrow ranges are viable** at $3K on Sui because gas is cheap (~$0.003). The bottleneck is swap fee (0.25%), not gas. Break-even after rebalance is under 2 hours even for conservative ranges.

2. **Dynamic range width** based on volatility (current project `dynamic` strategy) is the right approach. In calm periods, tighten to 240 ticks; in volatile periods, widen to 600 ticks.

3. **Rebalance aggressively** when price approaches range edge (10-15% threshold). The fast break-even means the cost of an extra rebalance is low compared to the cost of sitting out-of-range earning zero fees.

4. **Compound frequently** - with CETUS + SUI reward emissions on top of trading fees, compounding every 1-2 hours maximizes returns via reinvestment.

5. **Monitor volume trends** - the pool's daily volume ($2.8M-$5M) drives all fee calculations. If volume drops significantly, wider ranges become more appropriate to reduce rebalance frequency.

6. **Post-hack consideration** - Cetus TVL is still recovering. Lower TVL means less competition for fees (good for our position) but higher protocol risk. Maintain circuit breaker protections and don't increase position size beyond $3K until TVL stabilizes above $200M.

---

## Data Sources

- On-chain pool object via Sui mainnet RPC (`sui_getObject`)
- GeckoTerminal pool analytics
- DexPaprika pool analytics
- Cetus Developer Documentation
- DefiLlama protocol metrics
- Uniswap v3 concentrated liquidity research (comparable CLMM mechanics)
- Kamino/Kriya vault strategy documentation

*Analysis date: February 19, 2026*
