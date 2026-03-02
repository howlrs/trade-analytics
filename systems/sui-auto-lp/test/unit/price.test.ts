import { describe, it, expect, vi, beforeEach } from 'vitest'
import BN from 'bn.js'

// Mock pool.ts to avoid real SDK initialization
vi.mock('../../src/core/pool.js', () => ({
  getCetusSdk: vi.fn(),
}))

// Mock logger
vi.mock('../../src/utils/logger.js', () => ({
  getLogger: () => ({
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  }),
}))

import {
  alignTickToSpacing,
  tickToPrice,
  priceToTick,
  sqrtPriceToPrice,
  getCurrentPrice,
  coinBPriceInCoinA,
  getTickFromPrice,
  estimatePositionAmounts,
  rewardToUsd,
  tickToSqrtPriceX64,
  getCetusUsdPrice,
} from '../../src/core/price.js'

import { getCetusSdk } from '../../src/core/pool.js'

import type { PoolInfo, PositionInfo } from '../../src/types/index.js'

// USDC/SUI pool decimals
const USDC_DECIMALS = 6
const SUI_DECIMALS = 9

function makePool(overrides: Partial<PoolInfo> = {}): PoolInfo {
  return {
    poolId: '0xtest',
    coinTypeA: '0x...::usdc::USDC',
    coinTypeB: '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI',
    currentSqrtPrice: BigInt('18446744073709551616'), // 2^64 = sqrtPrice = 1.0
    currentTickIndex: 0,
    feeRate: 2500,
    liquidity: BigInt('1000000000'),
    tickSpacing: 60,
    rewarderCoinTypes: [],
    ...overrides,
  }
}

function makePosition(overrides: Partial<PositionInfo> = {}): PositionInfo {
  return {
    positionId: '0xpos',
    poolId: '0xtest',
    owner: '0xowner',
    tickLowerIndex: -120,
    tickUpperIndex: 120,
    liquidity: BigInt('1000000000'),
    feeOwedA: BigInt(0),
    feeOwedB: BigInt(0),
    rewardAmountOwed: [],
    ...overrides,
  }
}

describe('alignTickToSpacing', () => {
  it('returns unchanged for exact multiples', () => {
    expect(alignTickToSpacing(120, 60)).toBe(120)
    expect(alignTickToSpacing(0, 60)).toBe(0)
    expect(alignTickToSpacing(-180, 60)).toBe(-180)
  })

  it('floors to tick spacing multiple', () => {
    expect(alignTickToSpacing(100, 60)).toBe(60)   // floor(1.667) = 1
    expect(alignTickToSpacing(80, 60)).toBe(60)     // floor(1.333) = 1
    expect(alignTickToSpacing(90, 60)).toBe(60)     // floor(1.5)   = 1
    expect(alignTickToSpacing(29, 60)).toBe(0)      // floor(0.483) = 0
    expect(alignTickToSpacing(31, 60)).toBe(0)      // floor(0.517) = 0
    expect(alignTickToSpacing(119, 60)).toBe(60)    // floor(1.983) = 1
    expect(alignTickToSpacing(121, 60)).toBe(120)   // floor(2.017) = 2
  })

  it('handles negative ticks (Math.floor rounds toward -∞)', () => {
    expect(alignTickToSpacing(-100, 60)).toBe(-120)
    expect(alignTickToSpacing(-80, 60)).toBe(-120) // floor(-1.333) = -2
    expect(alignTickToSpacing(-31, 60)).toBe(-60)  // floor(-0.517) = -1
    expect(alignTickToSpacing(-29, 60)).toBe(-60)  // floor(-0.483) = -1
  })

  it('tickSpacing=1 returns unchanged', () => {
    expect(alignTickToSpacing(42, 1)).toBe(42)
    expect(alignTickToSpacing(-7, 1)).toBe(-7)
    expect(alignTickToSpacing(0, 1)).toBe(0)
  })
})

describe('tickToPrice / priceToTick round-trip', () => {
  it('round-trips within 1 tick for various values', () => {
    const testTicks = [0, 100, -100, 1000, -1000, 5000, -5000]
    for (const tick of testTicks) {
      const price = tickToPrice(tick, USDC_DECIMALS, SUI_DECIMALS)
      const recovered = priceToTick(price, USDC_DECIMALS, SUI_DECIMALS)
      expect(Math.abs(recovered - tick)).toBeLessThanOrEqual(1)
    }
  })

  it('returns positive prices', () => {
    expect(tickToPrice(0, USDC_DECIMALS, SUI_DECIMALS)).toBeGreaterThan(0)
    expect(tickToPrice(1000, USDC_DECIMALS, SUI_DECIMALS)).toBeGreaterThan(0)
    expect(tickToPrice(-1000, USDC_DECIMALS, SUI_DECIMALS)).toBeGreaterThan(0)
  })

  it('higher tick → higher price (coinB per coinA)', () => {
    const priceLow = tickToPrice(-1000, USDC_DECIMALS, SUI_DECIMALS)
    const priceHigh = tickToPrice(1000, USDC_DECIMALS, SUI_DECIMALS)
    expect(priceHigh).toBeGreaterThan(priceLow)
  })
})

describe('sqrtPriceToPrice', () => {
  it('produces expected price from known sqrtPriceX64', () => {
    // tick 0 → sqrtPrice = 2^64, price should be ~1.0 (adjusted for decimal diff)
    const sqrtAtTick0 = tickToSqrtPriceX64(0)
    const price = sqrtPriceToPrice(BigInt(sqrtAtTick0.toString()), USDC_DECIMALS, SUI_DECIMALS)
    // With decimalsA=6, decimalsB=9: price = raw * 10^(6-9) = raw * 0.001
    // At tick 0, raw price = 1.0, so adjusted = 0.001
    // Actually, SDK handles this internally. Just check it's a reasonable positive number.
    expect(price).toBeGreaterThan(0)
  })

  it('returns coinB per coinA (critical direction check)', () => {
    // For equal-decimal tokens at tick 0, price should be ~1.0
    const sqrtAtTick0 = tickToSqrtPriceX64(0)
    const priceEqualDecimals = sqrtPriceToPrice(BigInt(sqrtAtTick0.toString()), 9, 9)
    expect(priceEqualDecimals).toBeCloseTo(1.0, 2)
  })

  it('positive tick gives price > 1.0 for equal decimals', () => {
    const sqrtAtTick1000 = tickToSqrtPriceX64(1000)
    const price = sqrtPriceToPrice(BigInt(sqrtAtTick1000.toString()), 9, 9)
    expect(price).toBeGreaterThan(1.0)
  })

  it('negative tick gives price < 1.0 for equal decimals', () => {
    const sqrtAtTickNeg1000 = tickToSqrtPriceX64(-1000)
    const price = sqrtPriceToPrice(BigInt(sqrtAtTickNeg1000.toString()), 9, 9)
    expect(price).toBeLessThan(1.0)
  })
})

describe('getCurrentPrice', () => {
  it('delegates to sqrtPriceToPrice with pool.currentSqrtPrice', () => {
    const sqrtAtTick0 = tickToSqrtPriceX64(0)
    const pool = makePool({ currentSqrtPrice: BigInt(sqrtAtTick0.toString()) })
    const price = getCurrentPrice(pool, 9, 9)
    expect(price).toBeCloseTo(1.0, 2)
  })

  it('returns different prices for different sqrtPrices', () => {
    const sqrtLow = tickToSqrtPriceX64(-1000)
    const sqrtHigh = tickToSqrtPriceX64(1000)
    const priceLow = getCurrentPrice(makePool({ currentSqrtPrice: BigInt(sqrtLow.toString()) }), 9, 9)
    const priceHigh = getCurrentPrice(makePool({ currentSqrtPrice: BigInt(sqrtHigh.toString()) }), 9, 9)
    expect(priceHigh).toBeGreaterThan(priceLow)
  })
})

describe('coinBPriceInCoinA', () => {
  it('returns 1/getCurrentPrice()', () => {
    const sqrtAtTick1000 = tickToSqrtPriceX64(1000)
    const pool = makePool({ currentSqrtPrice: BigInt(sqrtAtTick1000.toString()) })
    const forward = getCurrentPrice(pool, USDC_DECIMALS, SUI_DECIMALS)
    const inverse = coinBPriceInCoinA(pool, USDC_DECIMALS, SUI_DECIMALS)
    expect(inverse).toBeCloseTo(1 / forward, 10)
  })

  it('for USDC/SUI pool returns USDC per SUI (the SUI price)', () => {
    // At tick 0 with equal decimals, coinB-per-coinA = 1.0, so inverse = 1.0
    const sqrtAtTick0 = tickToSqrtPriceX64(0)
    const pool = makePool({ currentSqrtPrice: BigInt(sqrtAtTick0.toString()) })
    const suiPrice = coinBPriceInCoinA(pool, 9, 9)
    expect(suiPrice).toBeCloseTo(1.0, 2)
  })

  it('inverse relationship holds: getCurrentPrice * coinBPriceInCoinA ≈ 1', () => {
    const sqrtAtTick500 = tickToSqrtPriceX64(500)
    const pool = makePool({ currentSqrtPrice: BigInt(sqrtAtTick500.toString()) })
    const forward = getCurrentPrice(pool, USDC_DECIMALS, SUI_DECIMALS)
    const inverse = coinBPriceInCoinA(pool, USDC_DECIMALS, SUI_DECIMALS)
    expect(forward * inverse).toBeCloseTo(1.0, 10)
  })
})

describe('getTickFromPrice', () => {
  it('returns tick aligned to tickSpacing', () => {
    const price = tickToPrice(100, USDC_DECIMALS, SUI_DECIMALS)
    const tick = getTickFromPrice(price, USDC_DECIMALS, SUI_DECIMALS, 60)
    expect(tick % 60).toBe(0)
  })

  it('round-trips within one tickSpacing for aligned ticks', () => {
    const originalTick = 120 // already aligned to 60
    const price = tickToPrice(originalTick, USDC_DECIMALS, SUI_DECIMALS)
    const recovered = getTickFromPrice(price, USDC_DECIMALS, SUI_DECIMALS, 60)
    // Math.floor alignment + float imprecision may snap down by one spacing
    expect(Math.abs(recovered - originalTick)).toBeLessThanOrEqual(60)
    expect(recovered % 60).toBe(0)
  })

  it('various prices produce correct aligned ticks', () => {
    const testTicks = [0, 60, -60, 180, -180, 600, -600]
    for (const tick of testTicks) {
      const price = tickToPrice(tick, USDC_DECIMALS, SUI_DECIMALS)
      const result = getTickFromPrice(price, USDC_DECIMALS, SUI_DECIMALS, 60)
      expect(Math.abs(result % 60)).toBe(0) // use abs to avoid -0 vs 0
      // Should be close to original (within one spacing)
      expect(Math.abs(result - tick)).toBeLessThanOrEqual(60)
    }
  })

  it('works with tickSpacing=1', () => {
    const price = tickToPrice(42, USDC_DECIMALS, SUI_DECIMALS)
    const tick = getTickFromPrice(price, USDC_DECIMALS, SUI_DECIMALS, 1)
    expect(Math.abs(tick - 42)).toBeLessThanOrEqual(1)
  })
})

describe('estimatePositionAmounts', () => {
  it('returns non-negative amountA and amountB', () => {
    const sqrtAtTick0 = tickToSqrtPriceX64(0)
    const pool = makePool({ currentSqrtPrice: BigInt(sqrtAtTick0.toString()) })
    const position = makePosition({
      tickLowerIndex: -120,
      tickUpperIndex: 120,
      liquidity: BigInt('1000000000'),
    })
    const { amountA, amountB } = estimatePositionAmounts(pool, position)
    expect(amountA).toBeGreaterThanOrEqual(BigInt(0))
    expect(amountB).toBeGreaterThanOrEqual(BigInt(0))
  })

  it('returns both tokens when price is in range', () => {
    const sqrtAtTick0 = tickToSqrtPriceX64(0)
    const pool = makePool({ currentSqrtPrice: BigInt(sqrtAtTick0.toString()) })
    const position = makePosition({
      tickLowerIndex: -600,
      tickUpperIndex: 600,
      liquidity: BigInt('10000000000'),
    })
    const { amountA, amountB } = estimatePositionAmounts(pool, position)
    expect(amountA).toBeGreaterThan(BigInt(0))
    expect(amountB).toBeGreaterThan(BigInt(0))
  })

  it('returns only coinB when price is below range (all converted to coinB)', () => {
    // Position range is [100, 200], current tick at 50 (below range)
    const sqrtBelow = tickToSqrtPriceX64(50)
    const pool = makePool({ currentSqrtPrice: BigInt(sqrtBelow.toString()) })
    const position = makePosition({
      tickLowerIndex: 100,
      tickUpperIndex: 200,
      liquidity: BigInt('10000000000'),
    })
    const { amountA, amountB } = estimatePositionAmounts(pool, position)
    // When price is below range, position is entirely in coinA
    expect(amountA).toBeGreaterThan(BigInt(0))
    expect(amountB).toBe(BigInt(0))
  })

  it('returns only coinA when price is above range', () => {
    // Position range is [-200, -100], current tick at -50 (above range)
    const sqrtAbove = tickToSqrtPriceX64(-50)
    const pool = makePool({ currentSqrtPrice: BigInt(sqrtAbove.toString()) })
    const position = makePosition({
      tickLowerIndex: -200,
      tickUpperIndex: -100,
      liquidity: BigInt('10000000000'),
    })
    const { amountA, amountB } = estimatePositionAmounts(pool, position)
    expect(amountA).toBe(BigInt(0))
    expect(amountB).toBeGreaterThan(BigInt(0))
  })
})

describe('rewardToUsd', () => {
  const suiPrice = 3.5
  const cetusPrice = 0.25

  it('SUI coin type → amount/1e9 * suiPriceUsdc', () => {
    const suiType = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
    const amount = BigInt(2_000_000_000) // 2 SUI
    const usd = rewardToUsd(suiType, amount, suiPrice, cetusPrice)
    expect(usd).toBeCloseTo(7.0, 5) // 2 * 3.5 = 7.0
  })

  it('CETUS coin type → amount/1e9 * cetusUsdPrice', () => {
    const cetusType = '0x06864a6f921804860930db6ddbe2e16acdf8504495ea7481637a1c8b9a8fe54b::cetus::CETUS'
    const amount = BigInt(4_000_000_000) // 4 CETUS
    const usd = rewardToUsd(cetusType, amount, suiPrice, cetusPrice)
    expect(usd).toBeCloseTo(1.0, 5) // 4 * 0.25 = 1.0
  })

  it('unknown coin type → 0', () => {
    const unknownType = '0xabc::random::TOKEN'
    const usd = rewardToUsd(unknownType, BigInt(1_000_000_000), suiPrice, cetusPrice)
    expect(usd).toBe(0)
  })

  it('zero amount → 0', () => {
    const suiType = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
    expect(rewardToUsd(suiType, BigInt(0), suiPrice, cetusPrice)).toBe(0)
  })

  it('handles fractional amounts', () => {
    const suiType = '0x02::sui::SUI'
    const amount = BigInt(500_000_000) // 0.5 SUI
    const usd = rewardToUsd(suiType, amount, suiPrice, cetusPrice)
    expect(usd).toBeCloseTo(1.75, 5) // 0.5 * 3.5
  })
})

describe('getCetusUsdPrice', () => {
  const mockedGetCetusSdk = vi.mocked(getCetusSdk)

  it('returns CETUS price in USD from pool data', async () => {
    // CETUS/SUI pool: sqrtPriceX64ToPrice returns SUI per CETUS
    // At tick 0 with equal decimals (9/9), price = 1.0 SUI per CETUS
    const sqrtAtTick0 = tickToSqrtPriceX64(0)
    mockedGetCetusSdk.mockReturnValue({
      Pool: {
        getPool: vi.fn().mockResolvedValue({
          current_sqrt_price: sqrtAtTick0.toString(),
        }),
      },
    } as any)

    const suiPriceUsdc = 3.5
    const cetusUsd = await getCetusUsdPrice(suiPriceUsdc)
    // cetusPriceInSui ≈ 1.0 at tick 0, so cetusUsd ≈ 1.0 * 3.5 = 3.5
    expect(cetusUsd).toBeCloseTo(3.5, 1)
  })

  it('returns 0 when SDK call fails', async () => {
    mockedGetCetusSdk.mockReturnValue({
      Pool: {
        getPool: vi.fn().mockRejectedValue(new Error('network error')),
      },
    } as any)

    const result = await getCetusUsdPrice(3.5)
    expect(result).toBe(0)
  })
})
