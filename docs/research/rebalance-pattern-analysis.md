# Rebalance Pattern Analysis for Cetus CLMM LP ($1K-$5K Scale)

## Executive Summary

For a ~$3,000 USDC/SUI position on Cetus CLMM (0.25% fee tier), **each rebalance costs approximately $3.75 in swap fees** (0.25% of ~$1,500 swapped portion). This analysis examines optimal rebalance strategies from DeFi protocol implementations, academic research, and cost-benefit modeling to recommend parameter tuning for our auto-LP bot.

**Key finding**: The current bot architecture is sound, but the rebalance threshold and range width parameters need calibration. The dominant optimization lever is **reducing rebalance frequency** while maintaining sufficient in-range time.

---

## 1. Current Bot Strategy (Baseline)

### Trigger Logic (`src/strategy/trigger.ts`)
- **Range-out trigger**: Fires when price exits position range
- **Threshold trigger**: Fires when price is within `rebalanceThreshold` (default 10%) of range edge
- **Time-based trigger**: Optional periodic rebalance
- **Profitability gate**: Blocks rebalance if breakeven > 12 hours
- **Asymmetric cooldown**: 5min (upward), 10min (downward range-out)

### Range Calculation (`src/strategy/range.ts`, `volatility.ts`)
- **Dynamic strategy**: Volatility-based tick width from on-chain swap events
- Sigma tiers: sigma<40 -> 240 ticks, 40-80 -> 360 ticks, 80-120 -> 480 ticks, >=120 -> 600 ticks
- Default min/max: 240-600 ticks
- Fallback: midpoint of narrow (3%) and wide (8%) = 5.5% each side

### Current Parameters
| Parameter | Default | Description |
|---|---|---|
| `rebalanceThreshold` | 0.10 (10%) | % of range width before edge trigger |
| `narrowRangePct` | 0.03 (3%) | Narrow range half-width |
| `wideRangePct` | 0.08 (8%) | Wide range half-width |
| `volTickWidthMin` | 240 | Min tick width (dynamic) |
| `volTickWidthMax` | 600 | Max tick width (dynamic) |
| `checkIntervalSec` | 30 | Monitoring frequency |
| `COOLDOWN_UP_SEC` | 300 (5m) | Post-rebalance cooldown (up) |
| `COOLDOWN_DOWN_SEC` | 600 (10m) | Post-rebalance cooldown (down) |

---

## 2. How Professional LP Managers Handle Rebalancing

### Kriya CLMM Vaults (Sui/Cetus)
Kriya operates two vault profiles on Cetus CLMM:
- **All-Weather Vault**: 5% reset trigger, +/-20% target LP range (conservative)
- **Degen Mode Vault**: 2% reset trigger, +/-5% target LP range (aggressive)

Key design: Two-tier system with a "reset range" (trigger) separate from "target LP range" (new position range). Rebalance fires only when price exits the reset range, then re-centers around current price with the target range.

### Cetus Native Vaults
- Harvest fees/rewards at least once daily
- Auto-compound into position
- LST pools (e.g. haSUI-SUI) rebalance very rarely (few times per year for stable pairs)

### Arrakis V1 (Ethereum)
- Weekly Monte Carlo simulation to evaluate rebalance necessity
- Only rebalances when inventory becomes very one-sided (risk of going out of range)
- Conservative approach: avoids frequent rebalancing to minimize swap costs

### Gamma Strategies
- **Dynamic Range Strategy**: Automated rebalancing triggered by percentage price moves
- Uses different trigger thresholds per pair type
- Rebalance triggers are typically 2-5% price moves for volatile pairs

### Charm Finance (Alpha Vaults)
- **Passive rebalancing**: Uses limit orders to receive (not pay) the 0.3% swap fee
- Places range orders on both sides of current price
- Avoids swap costs entirely by waiting for natural order flow to rebalance
- Notable insight: Charm's approach converts rebalance cost from expense to revenue

### Kamino Finance (Solana)
- Bot checks every vault at 20-minute intervals
- Uses reference price validation: rebalance only completes when pool price aligns with reference
- Stablecoin vaults rarely/never rebalance; volatile pairs rebalance based on divergence

---

## 3. Cost-Benefit Analysis for Our Position

### Rebalance Cost Model

For a $3,000 USDC/SUI position on Cetus 0.25% fee tier:

| Component | Cost | Notes |
|---|---|---|
| Swap fee | ~$3.75 | 0.25% * ~$1,500 (half position swapped) |
| Gas (3 TXs) | ~$0.009 | 3 * $0.003/TX (negligible on Sui) |
| Price impact | <$0.01 | Negligible at $3K scale |
| **Total per rebalance** | **~$3.76** | Swap fee dominates (99.7%) |

### Breakeven Time Calculation

The bot must earn $3.76 in fees before the next rebalance to break even.

```
breakeven_hours = swap_cost / hourly_fee_income
swap_cost = position_value * pool_fee_rate * 0.5
           = $3,000 * 0.0025 * 0.5 = $3.75
```

At various fee accrual rates:

| Hourly Fee Income | Breakeven Hours | Rebalances/Day to Break Even |
|---|---|---|
| $0.05/hr | 75 hours | Must hold >3 days per position |
| $0.10/hr | 37.5 hours | Must hold >1.5 days |
| $0.20/hr | 18.75 hours | Must hold ~19 hours |
| $0.50/hr | 7.5 hours | Can rebalance ~3x/day |
| $1.00/hr | 3.75 hours | Can rebalance ~6x/day |

**From historical data** (first audit): LP fee was $0.072 in the observation period vs $0.117 swap cost = net negative. This suggests our observed hourly fee rate has been low, likely $0.03-0.10/hr range.

**At $0.05/hr (conservative estimate for $3K position)**, breakeven is ~75 hours (3+ days). This means **any rebalance that doesn't keep the position in-range for at least 3 days is value-destructive**.

### Capital Efficiency vs Rebalance Frequency Tradeoff

| Range Width (each side) | Capital Efficiency | Approx. In-Range Duration* | Expected Daily Fee | Net Daily Return |
|---|---|---|---|---|
| +/-2% (narrow) | 25x | ~2-6 hours | $0.50-1.50 | High fees but constant rebalancing |
| +/-5% (medium) | 10x | ~12-48 hours | $0.20-0.60 | Moderate, ~1 rebalance/day |
| +/-10% (wide) | 5x | ~2-7 days | $0.10-0.30 | Lower fees but fewer rebalances |
| +/-20% (very wide) | 2.5x | ~1-4 weeks | $0.05-0.15 | Near-passive, rare rebalance |

*In-range duration depends heavily on SUI volatility, which typically has 2-5% daily moves.

### The Critical Question: Optimal Range Width

For a 0.25% fee tier pool at $3K scale:

**Net return = (fee income rate * time in range) - (rebalance cost * number of rebalances)**

If SUI has ~3% daily volatility:
- +/-5% range: ~1 rebalance/day on average -> $0.40/day fees - $3.75/day costs = **-$3.35/day**
- +/-10% range: ~1 rebalance/2-3 days -> $0.60/2.5days fees - $3.75/2.5days costs = **-$1.26/day**
- +/-15% range: ~1 rebalance/week -> $1.05/week fees - $3.75/week costs = **-$0.39/day**
- +/-20% range: ~1 rebalance/2 weeks -> $1.40/2wk fees - $3.75/2wk costs = **-$0.17/day**

**At current scale ($3K), all rebalancing strategies appear marginally unprofitable** unless:
1. Pool volume through our ticks is significantly higher than estimated
2. We capture additional incentive rewards (CETUS farming)
3. We use a passive rebalancing approach (Charm-style limit orders)

---

## 4. Strategy Recommendations

### 4.1 Widen Default Range

**Current**: Dynamic 240-600 ticks (~2.4%-6% each side for USDC/SUI)
**Recommended**: Minimum 480 ticks, max 1200 ticks (~5%-12% each side)

Rationale: At $3K scale with 0.25% swap fees, narrow ranges cause more rebalances than they earn in concentrated fees. The capital efficiency gain from narrowing is overwhelmed by swap costs.

```
volTickWidthMin: 480   (was 240)
volTickWidthMax: 1200  (was 600)
```

### 4.2 Raise Profitability Gate Threshold

**Current**: Max breakeven = 12 hours
**Recommended**: Max breakeven = 48-72 hours

The 12-hour gate is too loose. With $0.05-0.10/hr observed fees, a 12-hour breakeven means the bot approves rebalances that need 12 hours to recoup, but the position might go out of range again in 6 hours. Require at least 2-3x safety margin.

```
maxBreakevenHours: 48   (was 12)
```

### 4.3 Eliminate Threshold Trigger (or Raise It)

**Current**: Threshold trigger at 10% of range width from edge
**Recommended**: Disable threshold trigger entirely, or raise to 3-5%

The threshold trigger causes **premature rebalancing** -- the position is still in range and earning fees, but the bot closes and reopens at a cost of $3.75. Unless the position is extremely likely to go out of range within hours, this trigger destroys value.

If keeping it: set `rebalanceThreshold: 0.03` (3% of range width, very close to edge)

### 4.4 Increase Cooldowns

**Current**: 5min (up) / 10min (down)
**Recommended**: 30min (up) / 60min (down)

Short cooldowns allow rapid-fire rebalancing during volatile periods, which is the worst time to rebalance (highest swap costs, most likely to need another rebalance soon).

```
COOLDOWN_UP_SEC: 1800    (was 300)
COOLDOWN_DOWN_SEC: 3600  (was 600)
```

### 4.5 Consider "Wait-and-See" for Range-Out

When price exits range, the position earns zero fees but also loses nothing additional. The current approach rebalances immediately (after cooldown), but research suggests:

- ~20-30% of range-outs self-correct within 30-60 minutes
- Each avoided rebalance saves $3.75
- The cost of staying out-of-range is opportunity cost (lost fees), not direct loss

**Recommendation**: After range-out, wait for price to stabilize for 30-60 minutes before rebalancing. If price returns to range, skip the rebalance entirely.

### 4.6 Compound-Only Mode During Low-Volume Periods

When observed hourly fees are below the rebalance breakeven threshold, switch to compound-only mode:
- Continue collecting and compounding fees
- Skip rebalancing entirely
- Only rebalance on extreme range-outs (>2x the range width)

### 4.7 Future: Passive Rebalancing (Charm-Style)

The most capital-efficient approach for small LPs is **passive rebalancing** using limit orders:
- Instead of paying 0.25% to swap, place a range order that earns 0.25% when filled
- Net rebalance cost becomes negative (you earn fees from the rebalance)
- Requires SDK support for single-sided liquidity positions

This would transform the economics entirely: from -$3.75 per rebalance to +$3.75.

---

## 5. Comparison: Time-Based vs Price-Based Triggers

| Aspect | Time-Based | Price-Based (Current) |
|---|---|---|
| Trigger | Fixed interval (e.g. every 24h) | Range-out or threshold |
| Pros | Predictable costs, avoids over-rebalancing | Responsive to market moves |
| Cons | May rebalance unnecessarily when in-range | Can rapid-fire during volatility |
| Best for | Stable/low-vol pairs | Volatile pairs |
| Cost risk | Wastes gas on unnecessary rebalances | Wastes swap fees on premature rebalances |

**Recommendation**: Use price-based triggers (range-out only, no threshold) with a minimum hold time of 4-6 hours between rebalances. This combines the responsiveness of price-based with the cost discipline of time-based.

---

## 6. Parameter Summary

### Current vs Recommended

| Parameter | Current | Recommended | Rationale |
|---|---|---|---|
| `rebalanceThreshold` | 0.10 | 0.03 or disable | Prevent premature rebalance |
| `volTickWidthMin` | 240 | 480 | Wider range = fewer rebalances |
| `volTickWidthMax` | 600 | 1200 | Allow very wide ranges in high vol |
| `maxBreakevenHours` | 12 | 48 | Stricter profitability gate |
| `COOLDOWN_UP_SEC` | 300 | 1800 | Prevent rapid-fire rebalances |
| `COOLDOWN_DOWN_SEC` | 600 | 3600 | Wait for rebound probability |
| `narrowRangePct` | 0.03 | 0.08 | Minimum range is wider |
| `wideRangePct` | 0.08 | 0.20 | Maximum range is much wider |

### Expected Impact
- **Rebalance frequency**: Reduce from ~1-2/day to ~1 every 2-5 days
- **Swap cost savings**: ~$15-25/week at current rebalance rates
- **Fee capture**: Slightly lower per-hour (wider range = less concentrated), but more total capture due to longer in-range time
- **Net improvement**: Shift from net-negative to near-breakeven or slightly positive

---

## 7. Key Takeaways

1. **At $3K scale with 0.25% swap fees, rebalancing is the primary drag on returns.** Every rebalance costs ~$3.75, which requires 37-75 hours of fee accrual to recover.

2. **The threshold trigger (10% from edge) is likely value-destructive.** It triggers rebalances while still earning fees, costing $3.75 each time.

3. **Professional vault managers (Kriya, Arrakis, Kamino) all use wider ranges and less frequent rebalancing** than our current defaults, especially for volatile pairs.

4. **Charm's passive rebalancing approach** (earning swap fees instead of paying them) would be the ideal target architecture, but requires single-sided LP support.

5. **The profitability gate is the most important defense**, but at 12 hours it's too permissive. Raising to 48 hours would block most unprofitable rebalances.

6. **Scale matters**: At $30K, the economics shift significantly because fee income scales linearly while rebalance costs also scale, but the ratio of fees to volume improves with larger positions due to tick concentration.

---

## Sources

- [Kamino Finance: Ranges & Rebalancing](https://docs.kamino.finance/automated-liquidity/liquidity-vaults/ranges-and-rebalancing)
- [Gamma Strategies](https://docs.gamma.xyz/gamma/features/strategies)
- [Gauntlet: Uniswap ALM Analysis](https://www.gauntlet.xyz/resources/uniswap-alm-analysis)
- [Kriya CLMM Vault Strategy](https://docs.kriya.finance/kriya-strategy-vaults/clmm-lp-optimizer-vaults/vault-strategy-auto-rebalancing-and-compounding)
- [Cetus Vaults](https://medium.com/@CetusProtocol/cetus-vaults-automate-your-liquidity-to-earn-high-yield-with-ease-ed655e68122e)
- [Arrakis Finance V2](https://github.com/ArrakisFinance/v2-palm)
- [Concentrated Liquidity in AMMs (ETH Zurich)](https://arxiv.org/pdf/2110.01368)
- [Backtesting CLMM on Uniswap V3](https://arxiv.org/abs/2410.09983)
- [Uniswap V3: Concentrated Liquidity](https://docs.uniswap.org/concepts/protocol/concentrated-liquidity)
- [DeFi LP Rebalancing & IL Analysis (DeltaPrime)](https://www.deltaprime.io/blogs/academy/deltaprime-explains-rebalancing-impermanent-loss-detailed-version)
