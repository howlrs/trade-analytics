# Passive Rebalancing & Kriya Vault Deep Dive

## 1. Charm-Style Passive Rebalancing: Mechanism

### How It Works

Charm Finance's Alpha Vaults use a "passive rebalancing" strategy that **never executes swaps**. Instead of paying the 0.25% pool fee to rebalance token ratios, it relies on natural market order flow to gradually convert excess inventory.

The strategy maintains **three simultaneous liquidity positions**:

#### Position Structure

```
                    Current Price
                         |
   Full-Range Order      |      Full-Range Order
   ████████████████████████████████████████████
                         |
         Base Order      |      Base Order
         ████████████████████████████████
                         |
                  Limit Order (one-sided)
                  ████████|
                         |
```

1. **Full-Range Order** (parameter: `fullRangeWeight`, default 30%)
   - Covers the entire price range (like Uniswap v2)
   - Ensures the vault always has tradeable liquidity
   - Low capital efficiency but guarantees in-range status

2. **Base Order** (parameter: `baseThreshold`, measured in ticks)
   - Symmetric position centered on current price: `[price - B, price + B]`
   - Deposits liquidity in 50:50 ratio
   - Primary fee-earning position with concentrated efficiency
   - Any excess tokens that cannot be deposited 50:50 are held aside

3. **Limit Order** (parameter: `limitThreshold`, measured in ticks)
   - **Single-sided position** placed just above or below current price
   - Uses the excess tokens from the base order
   - If excess is token A: placed below price `[price - L, price]`
   - If excess is token B: placed above price `[price, price + L]`
   - As price moves through this range, excess tokens get naturally swapped by traders

#### The Key Insight

When the vault has imbalanced inventory (e.g., too much SUI after a downward move):
- Traditional approach: Swap SUI -> USDC, paying 0.25% fee = **-$3.75**
- Charm approach: Place excess SUI as a limit order below current price. Traders who buy SUI naturally fill this order, and the vault **earns** 0.25% fee = **+$3.75**

**Net difference per rebalance: $7.50** (from -$3.75 to +$3.75)

### Additional Charm Parameters

| Parameter | Description | Default |
|---|---|---|
| `fullRangeWeight` | % of vault in full-range position | 30% |
| `baseThreshold` | Base order half-width (ticks) | Varies by pair |
| `limitThreshold` | Limit order width (ticks) | Varies by pair |
| `period` | Min time between rebalances (seconds) | Varies |
| `twapDuration` | TWAP window for manipulation protection | 60 seconds |
| `maxTwapDeviation` | Max spot-TWAP deviation allowed | ~100 ticks (~1%) |
| `minTickMove` | Min price movement to justify rebalance gas | Varies |

### The Reversal Problem

Critical risk: If price crosses through a limit order range and then **reverses** before the position is withdrawn, the tokens get swapped back. The LP ends up with the original token mix, having earned some fees but failed to rebalance. This is manageable because:
- The vault still earns fees during the crossover
- It only delays rebalancing; it does not create losses
- The next rebalance attempt re-places the limit order

---

## 2. Can We Implement This on Cetus/Sui?

### Cetus Range Order Support

Cetus **does support range orders and single-sided liquidity** natively. From their documentation:

> "Range Order: You provide liquidity for a single-sided asset. It allows you to act as a maker and you can simulate limit orders."

The Cetus CLMM SDK's `createAddLiquidityFixTokenPayload` supports this:
- When `tick_lower` and `tick_upper` are **entirely below** current price: only coinA (USDC) is needed
- When `tick_lower` and `tick_upper` are **entirely above** current price: only coinB (SUI) is needed
- `ClmmPoolUtil.estLiquidityAndcoinAmountFromOneAmounts()` handles the single-sided calculation

### Implementation Feasibility

**What we can do today with the existing SDK:**

1. Open a position with tick range entirely above or below current price (single-sided)
2. Use `fix_amount_a: true` with `amount_b: '0'` for USDC-only below-price positions
3. Use `fix_amount_a: false` with `amount_a: '0'` for SUI-only above-price positions
4. Monitor when the position gets fully converted (price crossed the entire range)
5. Withdraw and re-deploy

**What we cannot easily do:**

1. Manage 3 simultaneous positions (base + limit + full-range) without significant state management
2. Atomically rebalance all 3 positions in a single transaction (would need PTB - Programmable Transaction Blocks)
3. Prevent the reversal problem without active monitoring

### Proposed Architecture: Simplified Passive Rebalance

Rather than the full Charm 3-position model, a simpler approach for our bot:

#### Strategy: "Passive-First, Active-Fallback"

```
On range-out detection:
  1. Calculate excess token (the one we have too much of)
  2. Open a LIMIT ORDER position (single-sided) just outside current price
     - If we have excess SUI: place below current price [price-L, price]
     - If we have excess USDC: place above current price [price, price+L]
  3. Keep the main position open (even though out of range)
  4. Wait up to PASSIVE_TIMEOUT hours for the limit order to fill
  5. If filled: close both, open new centered position (no swap needed!)
  6. If not filled after timeout: fall back to active rebalance (close + swap + open)
```

#### Implementation Sketch

```typescript
// New file: src/strategy/passive-rebalance.ts

interface PassiveRebalanceState {
  limitPositionId: string
  limitTickLower: number
  limitTickUpper: number
  direction: 'buy_a' | 'buy_b'  // what we're trying to acquire
  createdAt: number
  timeoutMs: number
}

async function attemptPassiveRebalance(
  pool: PoolInfo,
  position: PositionInfo,
  excessToken: 'a' | 'b',
  excessAmount: bigint,
  keypair: Ed25519Keypair,
): Promise<PassiveRebalanceState | null> {
  const tickSpacing = pool.tickSpacing
  const currentTick = pool.currentTickIndex

  let limitLower: number
  let limitUpper: number

  if (excessToken === 'b') {
    // Excess SUI: place below current price to sell SUI for USDC
    limitLower = alignTickToSpacing(currentTick - LIMIT_ORDER_WIDTH, tickSpacing)
    limitUpper = alignTickToSpacing(currentTick, tickSpacing)
  } else {
    // Excess USDC: place above current price to sell USDC for SUI
    limitLower = alignTickToSpacing(currentTick, tickSpacing)
    limitUpper = alignTickToSpacing(currentTick + LIMIT_ORDER_WIDTH, tickSpacing)
  }

  // Open single-sided position
  const result = await openPosition(
    pool.poolId,
    pool.coinTypeA, pool.coinTypeB,
    limitLower, limitUpper,
    excessToken === 'a' ? excessAmount.toString() : '0',
    excessToken === 'b' ? excessAmount.toString() : '0',
    slippage, keypair, false,
  )

  if (!result.success) return null

  return {
    limitPositionId: /* detect from TX */,
    limitTickLower: limitLower,
    limitTickUpper: limitUpper,
    direction: excessToken === 'b' ? 'buy_a' : 'buy_b',
    createdAt: Date.now(),
    timeoutMs: PASSIVE_TIMEOUT_MS,
  }
}
```

#### Expected Cost Savings

| Scenario | Active Rebalance | Passive Rebalance | Savings |
|---|---|---|---|
| Swap cost | -$3.75 | +$0 to +$3.75* | $3.75-$7.50 |
| Gas (3 TXs) | -$0.009 | -$0.012 (4 TXs) | -$0.003 |
| Time to complete | ~30 seconds | Minutes to hours | Slower |
| Certainty | 100% immediate | ~60-80% within 4h** | Lower |
| **Net per rebalance** | **-$3.76** | **$0 to +$3.74** | **$3.76-$7.50** |

*Earns swap fees from traders filling the limit order
**Depends on pool volume; USDC/SUI is high-volume

#### Risks and Mitigations

1. **Limit order not filled**: Set a timeout (4-6 hours). If unfilled, fall back to active rebalance. Net cost = same as current approach + small gas for the limit order position.

2. **Price reversal**: If price crosses through the limit order then reverses, tokens swap back. The limit order earns fees during both crossings, so it is not a loss. Re-attempt passive rebalance.

3. **Inventory split**: During passive rebalance, funds are split across the old position and the limit order. Neither earns optimal fees. The opportunity cost is the fee difference between concentrated and passive for the wait period.

4. **Complexity**: Managing 2+ positions simultaneously requires careful state tracking. Our existing position persistence (`data/positions.json`) would need extension.

---

## 3. Kriya Vault Deep Dive

### Vault Parameters (Confirmed)

| Parameter | All-Weather | Degen Mode |
|---|---|---|
| Reset Range (trigger) | 5% | 2% |
| Target LP Range | +/-20% | +/-5% |
| Auto-compound | Yes | Yes |
| Withdrawal fee | 1% | 1% |

### How Kriya's Two-Tier System Works

```
                    Current Price
                         |
   |----- Target LP Range (+/-20%) ------|
   |                     |               |
   |    |-- Reset Range (5%) --|         |
   |    |        |             |         |
   |    |   Current Price      |         |
   |    |        |             |         |
   |    |--------|-------------|         |
   |                     |               |
   |-----------------------------|-------|

When price moves 5% from center -> REBALANCE triggered
New position opened with +/-20% range centered on new price
```

**The insight**: The reset range (trigger) is much narrower than the target LP range. This means:
- Position only rebalances when price has moved significantly (5%)
- But the new position is placed with a very wide range (+/-20%)
- This wide range means the next rebalance likely won't happen for a long time
- Net effect: infrequent rebalancing with reasonable capital efficiency

### Kriya's Swap Cost Handling

Kriya does NOT use passive rebalancing. They handle swap costs through:
1. **Wide target ranges** (+/-20%): Reduces rebalance frequency dramatically
2. **Auto-compounding**: Reinvests swap fees and farming rewards to grow position size, offsetting rebalance costs over time
3. **Scale advantages**: Vault aggregates many users' funds, so the swap cost percentage is the same but the absolute fee income is much higher
4. **1% withdrawal fee**: Discourages short-term deposits that would dilute compounded gains

### Performance Claims

- "LP yields exceeding 2X of direct LPing" (from Kriya docs, unverified)
- TVL: ~$34.5M across all Kriya products
- No specific APR data publicly available for individual vaults

### What We Can Learn from Kriya

1. **The 5%/20% ratio is key**: Trigger at 5%, range at 20% = approximately 1 rebalance per 1-2 weeks for SUI/USDC
2. **At $3K scale, the All-Weather approach is better**: Degen Mode's narrow 5% range would require frequent rebalancing
3. **Compounding matters more than rebalancing**: Kriya compounds at least daily, which at scale generates significant additional returns
4. **The 1% withdrawal fee is a signal**: Even professional vault managers acknowledge that frequent entry/exit destroys value

---

## 4. Comparison: Implementation Options for Our Bot

### Option A: Widen Ranges (Quick Win, Low Risk)

Adopt Kriya All-Weather parameters:
- Trigger at 5% of range from edge (or disable threshold trigger)
- Target range: +/-15-20%
- Keep active swap-based rebalancing
- **Implementation effort**: Change config defaults only
- **Expected improvement**: Reduce rebalance from ~1/day to ~1/week
- **Savings**: ~$20-25/week in swap costs

### Option B: Passive-First Rebalancing (Medium Effort, High Impact)

Implement simplified Charm-style limit orders:
- On range-out, place single-sided limit order instead of swapping
- Timeout fallback to active rebalance after 4-6 hours
- **Implementation effort**: ~200-300 lines of new code + state management
- **Expected improvement**: Eliminate swap cost on ~60-80% of rebalances
- **Savings**: ~$2-6 per rebalance that fills passively

### Option C: Full Charm Model (High Effort, Highest Impact)

3-position strategy (base + limit + full-range):
- Requires managing multiple positions per pool
- Need PTB support for atomic multi-position operations
- **Implementation effort**: Major refactor, ~500+ lines
- **Expected improvement**: Near-zero swap costs, continuous fee earning
- **Savings**: Best possible, but complexity risk

### Recommendation

**Phase 1 (immediate)**: Option A -- widen ranges, raise profitability gate (from Task #2 recommendations)

**Phase 2 (next sprint)**: Option B -- passive-first rebalancing with active fallback. This captures most of the benefit with manageable complexity. Key implementation steps:
1. Add `PassiveRebalanceState` to position persistence
2. Modify `checkAndRebalance()` to attempt passive first
3. Add limit order monitoring to the check cycle
4. Add timeout-based fallback to active rebalance
5. Track passive vs active rebalance success rates in event log

**Phase 3 (future)**: Option C only if Phase 2 shows high passive fill rates (>80%) and we increase position size to $10K+.

---

## 5. Implementation Notes for Phase 2

### Key Code Changes Required

1. **`src/core/position.ts`**: The existing `openPosition()` already supports single-sided positions. When tick range is entirely above/below current price, setting one amount to '0' works with `fix_amount_a` toggling.

2. **`src/core/rebalance.ts`**: Add a `passiveRebalance()` path that:
   - Closes old position (same as today)
   - Instead of swap + open, opens a single-sided limit order + a base position
   - Returns a `PassiveRebalanceState` for monitoring

3. **`src/strategy/trigger.ts`**: Add a `checkPassiveRebalanceStatus()` function that:
   - Checks if the limit order position has been fully converted
   - If yes: close limit order, combine funds, open centered position
   - If timeout: fall back to active rebalance

4. **`src/scheduler.ts`**: Add passive rebalance state tracking to the check cycle

5. **`data/positions.json`**: Extend to track passive rebalance states:
   ```json
   {
     "passiveRebalances": {
       "poolId": {
         "limitPositionId": "0x...",
         "basePositionId": "0x...",
         "direction": "buy_a",
         "createdAt": 1708300000000,
         "timeoutMs": 14400000
       }
     }
   }
   ```

### Monitoring Metrics to Add

- `passive_rebalance_attempted`: Count of passive rebalance attempts
- `passive_rebalance_filled`: Count that filled naturally (success)
- `passive_rebalance_timeout`: Count that fell back to active
- `passive_fill_time_avg`: Average time to fill (hours)
- `passive_fee_earned`: Fees earned from limit orders during fill

---

## Sources

- [Charm Finance: Alpha Vaults Whitepaper](https://learn.charm.fi/charm/products-overview/alpha-vaults/whitepaper)
- [Charm Finance: Vault Parameters](https://learn.charm.fi/charm/manage-liquidity/user-guides/vault-parameters)
- [Charm Finance: Rebalancing](https://learn.charm.fi/charm/manage-liquidity/user-guides/rebalancing)
- [Uniswap v3: Range Orders](https://docs.uniswap.org/concepts/protocol/range-orders)
- [Uniswap: What is single-sided liquidity?](https://support.uniswap.org/hc/en-us/articles/20902968738317-What-is-single-sided-liquidity)
- [Cetus: CLMM Overview](https://cetus-1.gitbook.io/cetus-docs/protocol-concepts/concentrated-liquidity)
- [Cetus: Add Liquidity SDK](https://cetus-1.gitbook.io/cetus-developer-docs/developer/via-sdk/features-available/add-liquidity)
- [Kriya: CLMM LP Optimizer Vaults](https://docs.kriya.finance/kriya-strategy-vaults/clmm-lp-optimizer-vaults)
- [Kriya: Vault Strategy](https://docs.kriya.finance/kriya-strategy-vaults/clmm-lp-optimizer-vaults/vault-strategy-auto-rebalancing-and-compounding)
- [Kriya CLMM Yield Optimizer Vaults Launch](https://kriyadex.substack.com/p/kriya-clmm-yield-optimizer-vaults)
