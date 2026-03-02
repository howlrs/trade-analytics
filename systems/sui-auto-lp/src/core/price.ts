import { TickMath, ClmmPoolUtil, d } from '@cetusprotocol/cetus-sui-clmm-sdk'
import BN from 'bn.js'
import type { PoolInfo, PositionInfo } from '../types/index.js'
import { getCetusSdk } from './pool.js'
import { getLogger } from '../utils/logger.js'

// Well-known coin types
const SUI_COIN_TYPE = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
const CETUS_COIN_TYPE = '0x06864a6f921804860930db6ddbe2e16acdf8504495ea7481637a1c8b9a8fe54b::cetus::CETUS'

// CETUS/SUI pool on Cetus mainnet
const CETUS_SUI_POOL_ID = '0x2e041f3fd93646dcc877f783c1f2b7fa62d30271bdef1f21ef002cebf857bded'
const CETUS_DECIMALS = 9
const SUI_DECIMALS = 9

export function tickToPrice(tickIndex: number, decimalsA: number, decimalsB: number): number {
  return TickMath.tickIndexToPrice(tickIndex, decimalsA, decimalsB).toNumber()
}

export function priceToTick(price: number, decimalsA: number, decimalsB: number): number {
  return TickMath.priceToTickIndex(d(price), decimalsA, decimalsB)
}

/**
 * Raw CLMM price from sqrtPriceX64.
 * Returns coinB per coinA (e.g. SUI per USDC for a USDC/SUI pool).
 * Use this for tick-level comparisons where all values share the same unit.
 */
export function sqrtPriceToPrice(sqrtPriceX64: bigint, decimalsA: number, decimalsB: number): number {
  return TickMath.sqrtPriceX64ToPrice(new BN(sqrtPriceX64.toString()), decimalsA, decimalsB).toNumber()
}

/**
 * Raw CLMM price for the pool's current tick.
 * Returns coinB per coinA (e.g. SUI per USDC for a USDC/SUI pool).
 * For USD conversion, use {@link coinBPriceInCoinA} instead.
 */
export function getCurrentPrice(pool: PoolInfo, decimalsA: number, decimalsB: number): number {
  return sqrtPriceToPrice(pool.currentSqrtPrice, decimalsA, decimalsB)
}

/**
 * Price of coinB denominated in coinA.
 * For a USDC(A)/SUI(B) pool, returns "USDC per SUI" — the SUI price in USDC.
 *
 * getCurrentPrice() returns coinB-per-coinA (SUI per USDC = how many SUI you
 * get for 1 USDC). This function inverts it so callers can multiply a coinB
 * amount by the result to get the coinA (USD) value.
 */
export function coinBPriceInCoinA(pool: PoolInfo, decimalsA: number, decimalsB: number): number {
  return 1 / getCurrentPrice(pool, decimalsA, decimalsB)
}

export function getTickFromPrice(price: number, decimalsA: number, decimalsB: number, tickSpacing: number): number {
  const rawTick = priceToTick(price, decimalsA, decimalsB)
  return alignTickToSpacing(rawTick, tickSpacing)
}

export function alignTickToSpacing(tick: number, tickSpacing: number): number {
  return Math.floor(tick / tickSpacing) * tickSpacing
}

export function tickToSqrtPriceX64(tickIndex: number): BN {
  return TickMath.tickIndexToSqrtPriceX64(tickIndex)
}

/**
 * Estimate token amounts held in a position from its liquidity and tick range.
 * Used as a safe fallback when wallet delta is unavailable (e.g., dry-run mode).
 */
export function estimatePositionAmounts(
  pool: PoolInfo,
  position: PositionInfo,
): { amountA: bigint; amountB: bigint } {
  const liq = new BN(position.liquidity.toString())
  const curSqrt = new BN(pool.currentSqrtPrice.toString())
  const lowerSqrt = TickMath.tickIndexToSqrtPriceX64(position.tickLowerIndex)
  const upperSqrt = TickMath.tickIndexToSqrtPriceX64(position.tickUpperIndex)

  const { coinA, coinB } = ClmmPoolUtil.getCoinAmountFromLiquidity(
    liq, curSqrt, lowerSqrt, upperSqrt, true,
  )

  return {
    amountA: BigInt(coinA.toString()),
    amountB: BigInt(coinB.toString()),
  }
}

/**
 * Get CETUS price in USD by reading the CETUS/SUI pool price and
 * multiplying by the SUI/USDC price.
 */
export async function getCetusUsdPrice(suiPriceUsdc: number): Promise<number> {
  const log = getLogger()
  try {
    const sdk = getCetusSdk()
    const pool = await sdk.Pool.getPool(CETUS_SUI_POOL_ID)
    const sqrtPrice = new BN(pool.current_sqrt_price.toString())
    // CETUS/SUI pool: coinA=CETUS, coinB=SUI
    // sqrtPriceX64ToPrice returns coinB per coinA = SUI per CETUS
    // cetusPriceInSui = how many SUI 1 CETUS is worth
    // cetusUsd = (SUI per CETUS) × (USDC per SUI) = USDC per CETUS
    const cetusPriceInSui = TickMath.sqrtPriceX64ToPrice(sqrtPrice, CETUS_DECIMALS, SUI_DECIMALS).toNumber()
    const cetusUsd = cetusPriceInSui * suiPriceUsdc
    log.debug('CETUS price', { cetusPriceInSui, cetusUsd })
    return cetusUsd
  } catch (err) {
    log.warn('Failed to fetch CETUS price, using 0', {
      error: err instanceof Error ? err.message : String(err),
    })
    return 0
  }
}

/**
 * Convert a reward token amount to USD value.
 * Supports SUI and CETUS; unknown tokens return 0.
 */
export function rewardToUsd(
  coinType: string,
  amount: bigint,
  suiPriceUsdc: number,
  cetusUsdPrice: number,
): number {
  if (coinType.includes('::sui::SUI')) {
    return (Number(amount) / 1e9) * suiPriceUsdc
  }
  if (coinType.includes('::cetus::CETUS')) {
    return (Number(amount) / 1e9) * cetusUsdPrice
  }
  // Unknown token — can't price
  return 0
}
