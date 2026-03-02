# Position Expansion & Scaling Path Analysis

**Date**: 2026-02-19
**Context**: Follow-up to fee-compound-il-analysis.md

---

## 1. Position Expansion: Can We Avoid Close/Swap/Open?

### 1.1 The Fundamental CLMM Constraint

**Tick ranges are immutable on existing positions.** This is a protocol-level constraint shared by Uniswap v3, Cetus CLMM, and all forks:

- A position is defined by `(pool, tickLower, tickUpper, liquidity)`
- You can add/remove liquidity to an existing position, but the tick bounds never change
- To change the range, you **must** create a new position with new tick bounds

Cetus SDK confirms this:
- `createAddLiquidityFixTokenPayload()` with `is_open: false` adds to an existing position at its fixed ticks
- `createAddLiquidityFixTokenPayload()` with `is_open: true` creates a new position with new ticks
- There is no "modify tick range" operation in the SDK or on-chain contract

**Conclusion: There is no way to adjust tick range without closing and re-opening.** Our current close/swap/open flow is the standard approach.

### 1.2 How Professional Vaults Handle This

#### Mellow Pulse Strategy V2: "Position Expansion"

The term "position expansion" in the Gauntlet ALM analysis refers to Mellow's Pulse V2 strategy. Here is what it actually does:

1. When price approaches the range edge, instead of closing the position and opening a new one centered on current price...
2. It **widens the existing position** by opening a **second position** with expanded tick bounds on both sides
3. The old position remains active (still earning fees in its range)
4. The new wider position captures the price movement without requiring a swap
5. Only when the cumulative width exceeds a maximum threshold does it consolidate: close all positions, swap, and open one new centered position

**Why this reduces costs:**
- No swap needed when expanding (the new wider position accepts the current token ratio)
- IL is not "realized" because the old position stays open
- Fewer total rebalance events requiring swaps

**Can we implement this on Cetus?** Yes, in principle:
- Open a second position with wider ticks using existing wallet funds
- Keep the original position earning fees
- Periodically consolidate when managing multiple positions becomes unwieldy

**Practical challenges for our bot:**
- Requires managing multiple simultaneous positions (currently we track one per pool)
- Each expansion still costs gas for `openPosition` (~$0.003, negligible)
- When price moves far enough to require consolidation, we still pay the swap fee
- Position tracking and state management becomes more complex

### 1.3 Multi-Position Approach (Gamma Style)

Gamma uses multiple overlapping positions with different widths:
- **Base position**: Wide range (e.g., ±20%), captures fees in any moderate move
- **Limit position**: Narrow range on one side, acts as a limit order to accumulate the cheaper token during directional moves

This is more capital-efficient but requires:
- Splitting capital across positions
- Complex rebalancing logic
- At $3K, splitting capital further reduces per-position fee capture

### 1.4 Recommendation for Our Bot

**Short term (current $3K):** Keep the current close/swap/open approach but with wider ranges (per previous analysis). The complexity of multi-position management is not justified at this capital level.

**Medium term ($5K-$10K):** Consider implementing a simplified two-position strategy:
- **Core position** (80% of capital): Wide range (±15%), rarely rebalanced
- **Active position** (20% of capital): Narrower range (±5%), rebalanced more frequently
- Only the active position incurs swap costs on rebalance

**Long term ($10K+):** Implement Pulse V2-style expansion:
- Open additional positions to widen range instead of closing/reopening
- Consolidate periodically (e.g., weekly or when position count > 3)
- This reduces swap-triggered rebalances by 50-70%

---

## 2. Scaling Path: $3K to $10K+

### 2.1 How Returns Scale with Position Size

Fee revenue scales **linearly** with position size (your share of in-range liquidity is proportional to capital deployed). But costs scale differently:

| Cost Component | Scaling | Impact |
|---|---|---|
| Gas per TX | **Fixed** (~$0.009/rebalance) | Irrelevant at any size on Sui |
| Swap fee (0.25%) | **Linear** with swap amount | Always proportional to position |
| Rebalance frequency | **Constant** (price-driven) | Same number of rebalances regardless of size |
| IL (absolute) | **Linear** with position size | Proportional to capital |
| IL (percentage) | **Constant** | Same % IL regardless of size |

**Key insight: On Sui, returns scale linearly with position size.** There is no "sweet spot" where economics fundamentally change, because the dominant cost (0.25% swap fee) is percentage-based, not fixed.

This is different from Ethereum, where fixed gas costs create a threshold below which LP is unviable.

### 2.2 Break-Even Analysis by Position Size

Using our model: ±8% range, ~4 rebalances/month, $5M daily pool volume, 0.25% fee rate.

| Position Size | Monthly Fees (est.) | Monthly Swap Cost | Monthly Gas | Net Monthly | Net APR |
|---|---|---|---|---|---|
| $1,000 | $3.00 | $3.75 | $0.04 | **-$0.79** | -0.9% |
| $2,000 | $6.00 | $7.50 | $0.04 | **-$1.54** | -0.9% |
| $3,000 | $9.00 | $11.25 | $0.04 | **-$2.29** | -0.9% |
| $5,000 | $15.00 | $18.75 | $0.04 | **-$3.79** | -0.9% |
| $10,000 | $30.00 | $37.50 | $0.04 | **-$7.54** | -0.9% |

**Wait — at ±8% and 4 rebalances/month, the strategy is negative at ALL position sizes!**

This is because the swap fee is percentage-based. The break-even doesn't depend on size — it depends on the **fee/rebalance ratio**:

```
Profitable when: Monthly_Fees > Monthly_Swap_Costs
  i.e., Fee_Rate × Volume_Share × Days > Fee_Rate × 0.5 × Rebalances
  i.e., Monthly_Fees > 0.25% × 50% × Position × Rebalances

Simplifying: need daily fee income > (0.125% × Position × Rebalances) / 30
```

### 2.3 The Real Lever: Rebalance Frequency

The number of rebalances is what determines profitability, not position size:

| Range Width | Rebalances/Mo | Monthly Fee Rate | Monthly Swap Cost Rate | Net |
|---|---|---|---|---|
| ±3% | 20 | 0.75% | 2.5% | **-1.75%** |
| ±5% | 10 | 0.50% | 1.25% | **-0.75%** |
| ±8% | 4 | 0.30% | 0.50% | **-0.20%** |
| ±12% | 2 | 0.20% | 0.25% | **-0.05%** |
| ±15% | 1 | 0.15% | 0.125% | **+0.025%** |
| ±20% | 0.5 | 0.10% | 0.0625% | **+0.0375%** |

**At ±15% or wider, the strategy becomes marginally profitable regardless of position size.**

With CETUS rewards (if available), the break-even shifts earlier. But pure fee-based LP on USDC/SUI at current volumes requires very wide ranges.

### 2.4 What Changes at $10K+

While the percentage economics don't change, larger positions gain:

1. **Better liquidity share**: At $10K in a $30M pool, you're 0.033% of TVL. At $3K, you're 0.01%. Neither is significant, but larger positions get slightly better tick-level share.

2. **More viable multi-position strategies**: With $10K, a 80/20 split gives $8K core + $2K active. The $2K active position earns enough in fees to occasionally justify narrow-range rebalancing.

3. **Aggregator routing advantages**: Larger swaps may get better routing through the aggregator (multiple paths), reducing effective swap cost below 0.25%.

4. **Psychological viability**: At $10K with ±15% range, monthly fee income is ~$15. Not exciting, but at least positive.

### 2.5 When Does Active Management Beat Passive?

**Active narrow-range management becomes viable when:**
```
Fee_capture_boost × Daily_Fees > Extra_rebalance_cost

Where:
  Fee_capture_boost = (wide_range_width / narrow_range_width)  (capital efficiency ratio)
  Extra_rebalance_cost = additional_rebalances × 0.125% × Position_Value
```

For ±5% vs ±15% (3x capital efficiency):
```
Need: 3 × base_daily_fee × 30 > (10 - 1) × 0.125% × Position
  3 × base_fee × 30 > 9 × 0.00125 × Position
  90 × base_fee > 0.01125 × Position
  base_fee > 0.000125 × Position

At $3K:  need base_fee > $0.375/day → Total fee with narrow = $1.125/day
At $10K: need base_fee > $1.25/day  → Total fee with narrow = $3.75/day
At $50K: need base_fee > $6.25/day  → Total fee with narrow = $18.75/day
```

**At current pool volumes ($5M/day, $30M TVL), $3K position at ±15% earns ~$0.15/day.**
Narrow-range management would need $0.375/day base fee (i.e., 2.5x current volume or a different pool with higher volume/TVL ratio).

**Verdict: Active narrow management is not viable at any size with current USDC/SUI pool parameters.** The pool's volume-to-TVL ratio is too low. You'd need a pool with higher fee tier (0.3%+) or much higher relative volume.

---

## 3. Alternative Strategies Worth Considering

### 3.1 Single-Sided Deposit (Range Order)

Instead of providing balanced liquidity, use LP as a limit order:
- When SUI is cheap: deposit USDC-only in a range below current price (acts as a buy order)
- When SUI is expensive: deposit SUI-only in a range above current price (acts as a sell order)

Advantages:
- No swap needed (single token deposit)
- Acts as a DCA mechanism
- No IL in the traditional sense (you wanted to buy/sell anyway)

Disadvantages:
- Only earns fees when price is in your range
- Requires directional conviction

### 3.2 Higher Fee-Tier Pools

If Cetus has pools with higher fee tiers (0.5%, 1%), these may be more profitable for active LP:
- Higher fee per trade offsets rebalance costs faster
- Typically lower volume, but fee-per-rebalance ratio is better

### 3.3 Correlated Pairs

Providing LP on correlated pairs (e.g., USDC/USDT, staked-SUI/SUI) dramatically reduces:
- Rebalance frequency (price stays in tight range)
- IL (minimal price divergence)
- But also reduces fee income (tight range, low volume)

### 3.4 Fee Tier Arbitrage

Monitor multiple fee tiers of the same pair. When volume shifts to a different tier, migrate liquidity to capture more fees.

---

## 4. Implementation Roadmap

### Phase 1: Optimize Current Strategy (Now, $3K)
- Widen ranges to ±12-15% minimum
- Lower rebalance threshold to 0.05
- Add max-rebalances-per-day cap (3)
- Lower harvest threshold to $0.50
- **Expected outcome**: Marginal profitability or break-even on fees alone

### Phase 2: Two-Position Strategy ($5K-$10K)
- Core (80%): ±15-20% wide range, rarely rebalanced
- Active (20%): ±5-8%, actively managed for fee capture
- Compound from active to core position periodically
- **Implementation**: Extend scheduler to manage 2 positions per pool
- **Expected outcome**: +0.5-1% monthly from core, active position experiments

### Phase 3: Expansion Strategy ($10K+)
- Implement Pulse V2-style range expansion
- Open additional positions instead of close/swap/open
- Weekly consolidation cycle
- **Implementation**: New `expandPosition()` function, multi-position state tracking
- **Expected outcome**: 50-70% fewer swap-triggered rebalances

### Phase 4: Advanced ($20K+)
- Multi-pool LP across fee tiers
- Single-sided limit orders during high conviction
- Integration with Cetus Vaults SDK for automated strategies
- **Expected outcome**: Diversified fee sources, reduced single-pool risk

---

## 5. Summary

| Question | Answer |
|---|---|
| Can we adjust tick range without closing? | **No.** Immutable at protocol level. |
| Does position expansion exist? | **Yes**, via multi-position strategy (Pulse V2). Opens wider positions alongside existing ones. |
| Can we implement this on Cetus? | **Yes**, but adds complexity. Not justified at $3K. |
| At what size does narrow-range active LP work? | **Not viable at current pool volume/TVL ratios**, regardless of size. The pool fee-to-swap-cost ratio is the constraint, not position size. |
| Minimum size for current strategy profitability? | On Sui (cheap gas), profitability depends on range width, not size. At ±15%+, any size is marginally profitable. |
| How do returns scale? | **Linearly.** On Sui, both fees and swap costs are percentage-based. No threshold effect. |
| Best single optimization? | **Widen ranges to ±12-15%.** This has more impact than any amount of capital scaling. |
