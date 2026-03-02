# Fee, Compounding & Impermanent Loss Analysis

**Date**: 2026-02-19
**Position Size**: ~$3,000 (USDC/SUI on Cetus CLMM, 0.25% fee tier)
**Pool**: USDC/SUI 0.25% on Cetus (Sui network)

---

## 1. Current System State

### Current Parameters
| Parameter | Value | Source |
|---|---|---|
| Compound interval | 7200s (2h) | `COMPOUND_INTERVAL` |
| Harvest threshold | $3.00 USD | `HARVEST_THRESHOLD_USD` |
| Check interval | 30s | `CHECK_INTERVAL` |
| Rebalance threshold | 10% of range width | `REBALANCE_THRESHOLD` |
| Slippage tolerance | 1% | `SLIPPAGE_TOLERANCE` |
| Pool fee rate | 0.25% | Cetus USDC/SUI pool |
| Gas cost per TX | ~$0.003 | Sui network |

### First Audit Results (Historical)
- LP fee earned: $0.072
- Swap cost (rebalance): $0.117
- **Net result: -$0.045 (negative)**
- Root cause: swap pool fee (0.25%) dominated costs, not gas

---

## 2. Fee Revenue Projections

### Pool-Level Data (Cetus USDC/SUI 0.25%)
- Pool TVL: ~$30M
- 24h volume: ~$5M
- Pool-level 24h fees: $5M × 0.25% = $12,500/day

### Position-Level Fee Estimation

Fee revenue for a concentrated liquidity position depends on:
1. **Position size relative to active liquidity** (share of in-range liquidity)
2. **Capital efficiency** (how narrow the range is)
3. **Volume flowing through your ticks**

#### Formula
```
Daily Fee = (Position_Value / Active_Liquidity_In_Range) × Daily_Volume × Fee_Rate
```

#### Conservative Model (for $3K position)

| Range Width (%) | Capital Efficiency | Estimated Daily Fee | Annualized APR |
|---|---|---|---|
| ±3% (narrow) | ~16x | $0.50 - $1.50 | 6% - 18% |
| ±5% (medium) | ~10x | $0.30 - $0.90 | 3.6% - 11% |
| ±8% (wide) | ~6x | $0.18 - $0.55 | 2.2% - 6.7% |

**Key assumption**: You capture proportional volume through your tick range. In practice, large LPs dominate active ticks, reducing small-position share.

**Realistic estimate for $3K**: $0.30 - $0.80/day with a medium range (~±5%).

---

## 3. Compound Frequency Optimization

### The Compounding Math

For a position earning fees at rate `r` per period, with compounding cost `c`, the optimal compound frequency `n*` satisfies:

```
n* = sqrt(r_annual × Principal / (2 × c))
```

Where:
- `r_annual` = annual fee rate (e.g., 0.08 for 8%)
- `Principal` = $3,000
- `c` = cost per compound transaction

### Sui-Specific Analysis

On Sui, gas cost per compound is ~$0.003. But the real cost is:
- Gas: $0.003
- **No swap fee on compound** (fees are already in the correct token pair)
- So compound cost ≈ $0.003 (gas only)

This is critically different from rebalancing, where the 0.25% swap fee dominates.

### Optimal Compound Frequency Calculation

```
At 8% APR, $3K position:
- Annual fees = $240
- Per-hour fees = $0.027
- Per-2-hour fees = $0.055

Marginal benefit of compounding:
- Compound every 2h: APY = 8.33% (from 8% APR) → extra $9.87/year
- Compound every 24h: APY = 8.33% → extra $9.87/year (negligible difference)
- Compound daily: APY = 8.32% → extra $9.72/year

The difference between hourly and daily compounding at 8% APR:
  APY_hourly  = (1 + 0.08/8760)^8760 - 1 = 8.33%
  APY_daily   = (1 + 0.08/365)^365 - 1   = 8.33%
  APY_2hourly = (1 + 0.08/4380)^4380 - 1  = 8.33%

At 8% APR, the compounding frequency barely matters!
```

### When Compounding Matters

Compounding frequency only meaningfully affects returns at high APRs:

| APR | Daily APY | Hourly APY | Yearly Gain from Hourly vs Daily |
|---|---|---|---|
| 8% | 8.33% | 8.33% | $0.15 |
| 50% | 64.87% | 64.87% | $0.30 |
| 100% | 171.46% | 171.83% | $11.04 |
| 500% | 14,641% | 14,764% | $3,700 |

**At realistic APRs (5-15%), compounding frequency is nearly irrelevant for a $3K position.**

### Recommendation: Compound Frequency

The current 2-hour compound interval is **unnecessarily frequent** but harmless because:
1. Sui gas is so cheap ($0.003) that frequent checks cost almost nothing
2. The $3.00 harvest threshold correctly gates actual TX execution
3. The harvest-only fallback avoids wasteful addLiquidity calls for tiny amounts

**Recommended change**: Keep compound check at 2h, but **raise harvest threshold to match actual economics**:
- At $0.50/day fee rate: fees hit $1.00 in ~2 days
- At $0.30/day fee rate: fees hit $1.00 in ~3 days

**Recommendation**: Set `HARVEST_THRESHOLD_USD=1.0` (down from $3.0). At $3.0, you wait 4-10 days between harvests, forgoing the (admittedly small) compound benefit. At $1.0, you compound every 1-3 days, which is reasonable.

Actually, given the marginal compounding benefit is ~$0.15/year at 8% APR, the threshold barely matters. The real question is: **do you want fees in your position or in your wallet?**

- If fees stay uncollected, they earn **no additional yield** (they sit in the position but don't earn fees-on-fees)
- If compounded, they marginally increase your LP share
- If harvested to wallet, you can redeploy or use elsewhere

**Final recommendation**: `HARVEST_THRESHOLD_USD=0.50`. Compound when it's free (gas-only), don't overthink the frequency.

---

## 4. Impermanent Loss Analysis

### IL Formula for Concentrated Liquidity

For a position with range [p_a, p_b] centered at price p_0, when price moves to p_1:

**Standard AMM (v2) IL:**
```
IL_v2 = 2√(p_1/p_0) / (1 + p_1/p_0) - 1
```

**Concentrated Liquidity IL Amplification:**
```
IL_concentrated ≈ IL_v2 × (1 / range_width_fraction)
```

More precisely, the amplification factor for a range [p_a, p_b] is approximately:
```
Amplification ≈ √(p_0) / (√(p_b) - √(p_a))
```

For a ±5% range around price p:
- p_a = 0.95p, p_b = 1.05p
- Amplification ≈ 1 / (√1.05 - √0.95) ≈ 1/0.0499 ≈ 20x

### IL Scenarios for USDC/SUI

SUI daily volatility: ~5-6% (measured from recent data).

| Price Move | v2 IL | ±3% Range IL | ±5% Range IL | ±8% Range IL |
|---|---|---|---|---|
| ±5% | 0.06% | ~2.0% ($60) | ~1.2% ($36) | ~0.75% ($22) |
| ±10% | 0.23% | OUT OF RANGE | ~4.6% ($138) | ~2.9% ($87) |
| ±15% | 0.51% | OUT OF RANGE | OUT OF RANGE | ~6.4% ($192) |
| ±20% | 0.94% | OUT OF RANGE | OUT OF RANGE | OUT OF RANGE |

**Key insight**: With SUI's 5-6% daily volatility, a ±3% range will go out-of-range **almost daily**. A ±5% range lasts 1-2 days on average. ±8% gives 2-4 days.

### IL vs Fee Revenue Break-Even

The critical question: **How long must you stay in-range to earn back the IL?**

```
Break-even time = IL_at_exit / daily_fee_rate

For ±5% range, 5% price move:
  IL = $36
  Daily fee (optimistic) = $0.80
  Break-even = 45 days of uninterrupted in-range time

For ±8% range, 8% price move:
  IL = $22 (at boundary, then out of range)
  Daily fee = $0.45
  Break-even = 49 days
```

**This is why concentrated LP is challenging**: IL at range boundaries is large relative to fee income for small positions.

### IL Mitigation: The Rebalance Tradeoff

When price exits your range, you face a choice:
1. **Wait**: Earn 0 fees. If price returns, no realized IL. If it doesn't, opportunity cost.
2. **Rebalance**: Pay 0.25% swap fee + realize IL. Start earning fees again.

The current bot's profitability gate (`breakeven hours < 12h`) is a good heuristic:
- If expected fee income can recover swap cost within 12 hours, rebalance
- Otherwise, wait for price to return

### Directional IL and the USDC/SUI Pair

For USDC/SUI specifically:
- **Upward move (SUI appreciates)**: Position becomes mostly USDC. IL is real but you "sold SUI high."
- **Downward move (SUI depreciates)**: Position becomes mostly SUI. You "bought SUI on the way down."

The asymmetric cooldown in trigger.ts (5min up / 10min down) correctly reflects this: selling at the dip is more damaging than selling at the top.

---

## 5. Rebalance Cost Analysis

### Cost Per Rebalance

| Component | Cost | Notes |
|---|---|---|
| Close position TX | $0.003 | Gas only |
| Swap TX | $0.003 + 0.25% of swapped amount | Dominant cost |
| Open position TX | $0.003 | Gas only |
| **Total gas** | **$0.009** | Negligible |
| **Swap fee** (at $1,500 swap) | **$3.75** | ~50% of position swapped |
| **Total rebalance cost** | **~$3.76** | |

At $0.50/day fee income, each rebalance costs **7.5 days of fee revenue**.

### Rebalance Frequency vs Net Return

| Rebalances/Month | Monthly Swap Cost | Monthly Fees (est.) | Net Monthly |
|---|---|---|---|
| 1 | $3.76 | $15.00 | $11.24 |
| 2 | $7.52 | $15.00 | $7.48 |
| 4 | $15.04 | $15.00 | -$0.04 |
| 8 | $30.08 | $15.00 | -$15.08 |

**At ~4 rebalances/month, you break even. Above that, you lose money.**

Given SUI's 5-6% daily volatility and a ±5% range, you'd likely need 10-15 rebalances/month with a narrow range. This is why **wider ranges are essential for profitability**.

### Optimal Range Width

| Range | Est. Rebalances/Month | Monthly Fee | Monthly Swap Cost | Net |
|---|---|---|---|---|
| ±3% | 20-30 | $22.50 | $75-$112 | **-$52 to -$90** |
| ±5% | 8-15 | $15.00 | $30-$56 | **-$15 to -$41** |
| ±8% | 3-6 | $9.00 | $11-$22 | **-$2 to -$13** |
| ±12% | 1-3 | $6.00 | $3.75-$11 | **-$5 to +$2** |
| ±15% | 0.5-2 | $4.50 | $1.88-$7.52 | **-$3 to +$2.62** |

**Conclusion**: For a $3K position on USDC/SUI with 5-6% daily vol, profitability requires **wide ranges (±10-15%)** to minimize rebalances, even though fee capture per day is lower.

---

## 6. Protocol-Level Strategy Comparison

### How Professional Vaults Handle This

| Protocol | Strategy | Rebalance Trigger | Compound |
|---|---|---|---|
| **Gamma** | Dynamic width based on vol | Price exits range | Auto, batch TXs |
| **Arrakis** | Monte Carlo sim weekly | Weekly review | Auto within position |
| **Mellow** | Wide passive ranges | Rarely | Low-frequency |
| **Aperture** | Based on gas vs fee ratio | Cost-optimal timing | When fees > gas |

Key lessons:
1. **Gamma's Pulse V2** expands positions instead of close/swap/open, reducing swap costs
2. **Arrakis** uses weekly Monte Carlo — rebalances are rare
3. **Mellow** acknowledges that passive wide ranges often beat active narrow ones
4. All professional vaults **socialize gas** across many depositors — solo LPs can't do this

### Minimum Viable Position Size

From research:
- **Ethereum L1**: $10,000+ for viable auto-management (gas dominates)
- **L2/Alt-L1 (like Sui)**: $1,000+ can work because gas is cheap
- **BUT**: swap fee (0.25%) scales with position size — it's always 0.25% regardless of size

For Sui specifically, gas is not the constraint. The 0.25% swap fee per rebalance is. At $3K, each rebalance costs ~$3.75 in swap fees alone.

---

## 7. Specific Parameter Recommendations

### Compound Parameters

| Parameter | Current | Recommended | Rationale |
|---|---|---|---|
| `COMPOUND_INTERVAL` | 7200 (2h) | **7200 (keep)** | Check frequency is fine; threshold gates execution |
| `HARVEST_THRESHOLD_USD` | 3.0 | **0.50** | At $0.50/day, $3 threshold means 6-day wait. $0.50 allows ~daily compounds with minimal gas cost |

### Rebalance Parameters

| Parameter | Current | Recommended | Rationale |
|---|---|---|---|
| `REBALANCE_THRESHOLD` | 0.10 | **0.05** | Trigger closer to edge; let it ride longer |
| `volTickWidthMin` | 240 | **360** | Wider minimum to reduce rebalances |
| `volTickWidthMax` | 600 | **900** | Wider max for high-vol periods |
| `narrowRangePct` | 0.03 | **0.08** | 3% is too narrow for SUI volatility |
| `wideRangePct` | 0.08 | **0.15** | 8% still triggers too many rebalances |

### Profitability Gate

The existing profitability gate in `trigger.ts` (breakeven < 12h) should be **tightened**:

| Parameter | Current | Recommended | Rationale |
|---|---|---|---|
| `maxBreakevenHours` | 12 | **8** | Be more conservative; only rebalance when clearly profitable |
| Asymmetric cooldown (down) | 600s (10min) | **1200s (20min)** | Give more time for downward bounces |
| Asymmetric cooldown (up) | 300s (5min) | **300s (keep)** | Upward range-out is less risky |

### New Suggested Parameters

1. **Max rebalances per day**: Cap at 3. Beyond that, the position is likely in a whipsaw and should wait.
2. **Minimum time in range**: Don't rebalance if the position was in range for less than 2 hours (insufficient fee accrual to justify costs).

---

## 8. IL Measurement Implementation

### Tracking IL On-Chain

To measure actual IL, track these per position lifecycle:

```
Initial deposit: amount_A_in, amount_B_in, price_at_entry
Current/Exit:    amount_A_out, amount_B_out, price_at_exit

HODL value = amount_A_in + amount_B_in × (price_at_exit / price_at_entry)
LP value   = amount_A_out + amount_B_out × price_at_exit
IL         = LP value - HODL value
Net PnL    = LP value + total_fees_collected - HODL value - total_gas_spent - total_swap_fees
```

The bot already tracks most of these in event logs. Adding a cumulative P&L tracker per position lifecycle would provide ongoing IL visibility.

### Hedge Strategies (for reference, not recommended at $3K scale)

1. **Perp short hedge**: Short SUI on a perp DEX to neutralize directional exposure. Not practical at $3K (funding costs, management complexity).
2. **Options**: Buy SUI puts to protect downside. Not available at reasonable cost on Sui.
3. **Wider ranges**: The simplest "hedge" — reduces IL amplification at the cost of lower fee capture.

---

## 9. Summary & Key Takeaways

1. **Compounding is nearly irrelevant** at $3K / 8% APR. The difference between daily and hourly compounding is <$1/year. Lower the harvest threshold to $0.50 for convenience, not for compounding alpha.

2. **Rebalance cost is the critical bottleneck**. Each rebalance costs ~$3.75 (0.25% swap fee on ~$1,500). At $0.50/day fee income, that's 7.5 days to recover.

3. **Wider ranges are more profitable** despite lower per-tick fee capture. At SUI's 5-6% daily volatility, a ±3% range rebalances too often to be profitable. Target ±10-15% ranges.

4. **Maximum profitable rebalances**: ~3-4 per month at current fee rates. Beyond that, swap costs eat all profit.

5. **IL is real but manageable** with wider ranges. The asymmetric cooldown is a good directional hedge. Consider extending the downward cooldown further.

6. **The profitability gate is the most important feature**. The observed-fee-based breakeven calculation in trigger.ts is sound. Tighten the max breakeven hours from 12 to 8.

7. **At $3K, passive wide ranges beat active narrow management.** Professional vaults succeed because they socialize costs across many depositors and use advanced position expansion (not close/swap/open). Solo LP at $3K should prioritize minimizing transaction count.
