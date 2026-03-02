import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { PoolInfo } from '../../src/types/index.js'
import type { PoolConfig } from '../../src/types/config.js'

// ── Mocks ───────────────────────────────────────────────────────────

vi.mock('../../src/utils/logger.js', () => ({
  getLogger: () => ({ info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() }),
}))

const mockGetCurrentPrice = vi.fn()
const mockTickToPrice = vi.fn()
const mockGetTickFromPrice = vi.fn()
const mockAlignTickToSpacing = vi.fn()
const mockCoinBPriceInCoinA = vi.fn()

vi.mock('../../src/core/price.js', () => ({
  getCurrentPrice: (...args: any[]) => mockGetCurrentPrice(...args),
  tickToPrice: (...args: any[]) => mockTickToPrice(...args),
  getTickFromPrice: (...args: any[]) => mockGetTickFromPrice(...args),
  alignTickToSpacing: (...args: any[]) => mockAlignTickToSpacing(...args),
  coinBPriceInCoinA: (...args: any[]) => mockCoinBPriceInCoinA(...args),
  tickToSqrtPriceX64: vi.fn(),
}))

const mockCalculateDepositRatioFixTokenA = vi.fn()

vi.mock('@cetusprotocol/cetus-sui-clmm-sdk', () => ({
  ClmmPoolUtil: {
    calculateDepositRatioFixTokenA: (...args: any[]) => mockCalculateDepositRatioFixTokenA(...args),
  },
}))

// ── Helpers ─────────────────────────────────────────────────────────

function makePool(overrides: Partial<PoolInfo> = {}): PoolInfo {
  return {
    poolId: '0xpool1',
    coinTypeA: '0x...usdc',
    coinTypeB: '0x...sui',
    currentSqrtPrice: 18446744073709551616n, // arbitrary
    currentTickIndex: 1000,
    feeRate: 2500,
    liquidity: 1000000000n,
    tickSpacing: 60,
    rewarderCoinTypes: [],
    ...overrides,
  }
}

function makePoolConfig(overrides: Partial<PoolConfig> = {}): PoolConfig {
  return {
    poolId: '0xpool1',
    strategy: 'narrow',
    narrowRangePct: 0.03,
    wideRangePct: 0.08,
    volLookbackHours: 2,
    volTickWidthMin: 240,
    volTickWidthMax: 600,
    ...overrides,
  }
}

const DECIMALS_A = 6 // USDC
const DECIMALS_B = 9 // SUI

// ── calculateOptimalRange ───────────────────────────────────────────

describe('calculateOptimalRange', () => {
  let calculateOptimalRange: typeof import('../../src/strategy/range.js').calculateOptimalRange

  beforeEach(async () => {
    vi.clearAllMocks()
    // Default mock behavior
    mockGetCurrentPrice.mockReturnValue(1.05) // coinB per coinA
    mockTickToPrice.mockImplementation((tick: number) => 1.05 + tick * 0.0001)
    mockGetTickFromPrice.mockImplementation((price: number, _dA: number, _dB: number, spacing: number) => {
      const rawTick = Math.round((price - 1.05) / 0.0001)
      return Math.round(rawTick / spacing) * spacing
    })
    mockAlignTickToSpacing.mockImplementation((tick: number, spacing: number) => {
      return Math.floor(tick / spacing) * spacing
    })

    const mod = await import('../../src/strategy/range.js')
    calculateOptimalRange = mod.calculateOptimalRange
  })

  it('narrow strategy: uses narrowRangePct', () => {
    const pool = makePool()
    const config = makePoolConfig({ strategy: 'narrow', narrowRangePct: 0.03 })

    const result = calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B)

    expect(result.strategy).toBe('narrow')
    // getCurrentPrice returns 1.05, rangePct=0.03
    // priceLower = 1.05 * (1 - 0.03) = 1.0185
    // priceUpper = 1.05 * (1 + 0.03) = 1.0815
    expect(mockGetCurrentPrice).toHaveBeenCalledWith(pool, DECIMALS_A, DECIMALS_B)
    expect(mockGetTickFromPrice).toHaveBeenCalledTimes(2)
    // First call is for priceLower
    expect(mockGetTickFromPrice.mock.calls[0][0]).toBeCloseTo(1.05 * 0.97, 4)
    // Second call is for priceUpper
    expect(mockGetTickFromPrice.mock.calls[1][0]).toBeCloseTo(1.05 * 1.03, 4)
  })

  it('wide strategy: uses wideRangePct', () => {
    const pool = makePool()
    const config = makePoolConfig({ strategy: 'wide', wideRangePct: 0.08 })

    const result = calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B)

    expect(result.strategy).toBe('wide')
    expect(mockGetTickFromPrice.mock.calls[0][0]).toBeCloseTo(1.05 * 0.92, 4)
    expect(mockGetTickFromPrice.mock.calls[1][0]).toBeCloseTo(1.05 * 1.08, 4)
  })

  it('dynamic without volatility: uses midpoint of narrow+wide pct', () => {
    const pool = makePool()
    const config = makePoolConfig({ strategy: 'dynamic', narrowRangePct: 0.03, wideRangePct: 0.08 })

    const result = calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B)

    expect(result.strategy).toBe('dynamic')
    // midpoint = (0.03 + 0.08) / 2 = 0.055
    expect(mockGetTickFromPrice.mock.calls[0][0]).toBeCloseTo(1.05 * (1 - 0.055), 4)
    expect(mockGetTickFromPrice.mock.calls[1][0]).toBeCloseTo(1.05 * (1 + 0.055), 4)
  })

  it('dynamic with volatilityTickWidth: uses tick-based centered on currentTickIndex', () => {
    const pool = makePool({ currentTickIndex: 1000, tickSpacing: 60 })
    const config = makePoolConfig({ strategy: 'dynamic' })

    const result = calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B, 240)

    expect(result.strategy).toBe('dynamic')
    // halfWidth = floor(240 / 2) = 120
    // tickLower = alignTickToSpacing(1000 - 120, 60) = alignTickToSpacing(880, 60) = 840
    // tickUpper = alignTickToSpacing(1000 + 120, 60) = alignTickToSpacing(1120, 60) = 1080
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(880, 60)
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(1120, 60)
    // getTickFromPrice should NOT be called for volatility path
    expect(mockGetTickFromPrice).not.toHaveBeenCalled()
  })

  it('tick alignment respects tickSpacing', () => {
    const pool = makePool({ currentTickIndex: 1000, tickSpacing: 60 })
    const config = makePoolConfig({ strategy: 'dynamic' })

    calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B, 200)

    // halfWidth = floor(200 / 2) = 100
    // raw ticks: 900, 1100
    // aligned: floor(900/60)*60=900, floor(1100/60)*60=1080
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(900, 60)
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(1100, 60)
  })

  it('returns correct priceLower/priceUpper from tickToPrice', () => {
    mockTickToPrice.mockReturnValueOnce(0.95).mockReturnValueOnce(1.15)
    const pool = makePool()
    const config = makePoolConfig({ strategy: 'narrow' })

    const result = calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B)

    expect(result.priceLower).toBe(0.95)
    expect(result.priceUpper).toBe(1.15)
  })

  it('returns tickLower and tickUpper from getTickFromPrice', () => {
    mockGetTickFromPrice.mockReturnValueOnce(-120).mockReturnValueOnce(180)
    mockTickToPrice.mockReturnValueOnce(0.9).mockReturnValueOnce(1.2)
    const pool = makePool()
    const config = makePoolConfig({ strategy: 'narrow' })

    const result = calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B)

    expect(result.tickLower).toBe(-120)
    expect(result.tickUpper).toBe(180)
  })

  it('unknown strategy defaults to narrowRangePct', () => {
    const pool = makePool()
    const config = makePoolConfig({ strategy: 'something-else' as any, narrowRangePct: 0.03 })

    const result = calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B)

    // Should use narrowRangePct = 0.03 (same as narrow strategy)
    expect(mockGetTickFromPrice.mock.calls[0][0]).toBeCloseTo(1.05 * 0.97, 4)
    expect(mockGetTickFromPrice.mock.calls[1][0]).toBeCloseTo(1.05 * 1.03, 4)
  })

  // --- Regime multiplier tests ---

  it('compression regime (0.75x) narrows tick width', () => {
    const pool = makePool({ currentTickIndex: 1000, tickSpacing: 60 })
    const config = makePoolConfig({ strategy: 'dynamic', regimeEnabled: true, volTickWidthMin: 480, volTickWidthMax: 1200 })

    const regimeState = { regime: 'low' as const, isCompression: true, isTransition: false, currentSigma: 10 }
    calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B, 800, regimeState)

    // adjustedWidth = round(800 * 0.75) = 600, halfWidth = 300
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(700, 60) // 1000 - 300
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(1300, 60) // 1000 + 300
  })

  it('high vol regime (1.15x) widens tick width', () => {
    const pool = makePool({ currentTickIndex: 1000, tickSpacing: 60 })
    const config = makePoolConfig({ strategy: 'dynamic', regimeEnabled: true, volTickWidthMin: 480, volTickWidthMax: 1200 })

    const regimeState = { regime: 'high' as const, isCompression: false, isTransition: false, currentSigma: 150 }
    calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B, 800, regimeState)

    // adjustedWidth = round(800 * 1.15) = 920, halfWidth = 460
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(540, 60) // 1000 - 460
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(1460, 60) // 1000 + 460
  })

  it('transition regime (1.3x) widens tick width further', () => {
    const pool = makePool({ currentTickIndex: 1000, tickSpacing: 60 })
    const config = makePoolConfig({ strategy: 'dynamic', regimeEnabled: true, volTickWidthMin: 480, volTickWidthMax: 1200 })

    const regimeState = { regime: 'high' as const, isCompression: false, isTransition: true, currentSigma: 200 }
    calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B, 800, regimeState)

    // adjustedWidth = round(800 * 1.3) = 1040, halfWidth = 520
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(480, 60) // 1000 - 520
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(1520, 60) // 1000 + 520
  })

  it('mid regime (1.0x) does not change tick width', () => {
    const pool = makePool({ currentTickIndex: 1000, tickSpacing: 60 })
    const config = makePoolConfig({ strategy: 'dynamic', regimeEnabled: true, volTickWidthMin: 480, volTickWidthMax: 1200 })

    const regimeState = { regime: 'mid' as const, isCompression: false, isTransition: false, currentSigma: 60 }
    calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B, 800, regimeState)

    // adjustedWidth = 800 * 1.0 = 800, halfWidth = 400
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(600, 60) // 1000 - 400
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(1400, 60) // 1000 + 400
  })

  it('regimeEnabled=false ignores regime multiplier', () => {
    const pool = makePool({ currentTickIndex: 1000, tickSpacing: 60 })
    const config = makePoolConfig({ strategy: 'dynamic', regimeEnabled: false, volTickWidthMin: 480, volTickWidthMax: 1200 })

    const regimeState = { regime: 'low' as const, isCompression: true, isTransition: false, currentSigma: 10 }
    calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B, 800, regimeState)

    // No multiplier applied: halfWidth = 400
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(600, 60) // 1000 - 400
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(1400, 60) // 1000 + 400
  })

  it('regime-adjusted width is clamped to volTickWidthMax', () => {
    const pool = makePool({ currentTickIndex: 1000, tickSpacing: 60 })
    const config = makePoolConfig({ strategy: 'dynamic', regimeEnabled: true, volTickWidthMin: 480, volTickWidthMax: 1200 })

    // 1100 * 1.3 = 1430, but clamped to 1200
    const regimeState = { regime: 'high' as const, isCompression: false, isTransition: true, currentSigma: 200 }
    calculateOptimalRange(pool, config, DECIMALS_A, DECIMALS_B, 1100, regimeState)

    // adjustedWidth = min(1430, 1200) = 1200, halfWidth = 600
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(400, 60) // 1000 - 600
    expect(mockAlignTickToSpacing).toHaveBeenCalledWith(1600, 60) // 1000 + 600
  })
})

// ── calculateSwapPlan ───────────────────────────────────────────────

describe('calculateSwapPlan', () => {
  let calculateSwapPlan: typeof import('../../src/core/swap.js').calculateSwapPlan

  beforeEach(async () => {
    vi.clearAllMocks()
    // Default: SUI price = $4.00 in USDC terms
    mockCoinBPriceInCoinA.mockReturnValue(4.0)
    // Default deposit ratio: 50/50
    mockCalculateDepositRatioFixTokenA.mockReturnValue({
      ratioA: { toNumber: () => 0.5 },
      ratioB: { toNumber: () => 0.5 },
    })

    const mod = await import('../../src/core/swap.js')
    calculateSwapPlan = mod.calculateSwapPlan
  })

  it('no swap needed when imbalance < $1', () => {
    // balanceA=10 USDC, balanceB=2.5 SUI (=$10 USDC) → total $20, 50/50 target
    // targetA=$10, valueA=$10, diff=0
    const pool = makePool()
    const balanceA = 10_000000n  // 10 USDC (6 decimals)
    const balanceB = 2_500000000n // 2.5 SUI (9 decimals)

    const plan = calculateSwapPlan(pool, -100, 100, balanceA, balanceB, DECIMALS_A, DECIMALS_B)

    expect(plan.needSwap).toBe(false)
    expect(plan.swapAmount).toBe(0n)
    expect(plan.reason).toContain('skip swap')
  })

  it('need more USDC → swap SUI to USDC (a2b=false)', () => {
    // balanceA=2 USDC, balanceB=4.5 SUI (=$18 USDC) → total $20
    // 50/50 target: targetA=$10, valueA=$2, diffA=+8 → need more USDC
    const pool = makePool()
    const balanceA = 2_000000n    // 2 USDC
    const balanceB = 4_500000000n // 4.5 SUI

    const plan = calculateSwapPlan(pool, -100, 100, balanceA, balanceB, DECIMALS_A, DECIMALS_B)

    expect(plan.needSwap).toBe(true)
    expect(plan.a2b).toBe(false) // SUI→USDC
    expect(plan.swapAmount).toBeGreaterThan(0n)
    expect(plan.reason).toContain('SUI')
    expect(plan.reason).toContain('USDC')
  })

  it('need more SUI → swap USDC to SUI (a2b=true)', () => {
    // balanceA=18 USDC, balanceB=0.5 SUI (=$2 USDC) → total $20
    // 50/50 target: targetA=$10, valueA=$18, diffA=-8 → need more SUI
    const pool = makePool()
    const balanceA = 18_000000n   // 18 USDC
    const balanceB = 500000000n   // 0.5 SUI

    const plan = calculateSwapPlan(pool, -100, 100, balanceA, balanceB, DECIMALS_A, DECIMALS_B)

    expect(plan.needSwap).toBe(true)
    expect(plan.a2b).toBe(true) // USDC→SUI
    expect(plan.swapAmount).toBeGreaterThan(0n)
    expect(plan.reason).toContain('USDC')
    expect(plan.reason).toContain('SUI')
  })

  it('edge: 0 balance in token A', () => {
    // balanceA=0 USDC, balanceB=5 SUI (=$20) → total $20
    // 50/50 target: targetA=$10, need swap SUI→USDC
    const pool = makePool()
    const balanceA = 0n
    const balanceB = 5_000000000n // 5 SUI

    const plan = calculateSwapPlan(pool, -100, 100, balanceA, balanceB, DECIMALS_A, DECIMALS_B)

    expect(plan.needSwap).toBe(true)
    expect(plan.a2b).toBe(false) // SUI→USDC
  })

  it('edge: 0 balance in token B', () => {
    // balanceA=20 USDC, balanceB=0 SUI → total $20
    // 50/50 target: targetA=$10, need swap USDC→SUI
    const pool = makePool()
    const balanceA = 20_000000n // 20 USDC
    const balanceB = 0n

    const plan = calculateSwapPlan(pool, -100, 100, balanceA, balanceB, DECIMALS_A, DECIMALS_B)

    expect(plan.needSwap).toBe(true)
    expect(plan.a2b).toBe(true) // USDC→SUI
  })

  it('target amounts calculated correctly for 50/50 ratio', () => {
    // balanceA=10 USDC, balanceB=2.5 SUI (=$10 USDC) → total $20
    // 50/50 → targetValueA=$10, targetValueB=$10
    // targetAmountA = 10 * 10^6 = 10_000000
    // targetAmountB = (10 / 4.0) * 10^9 = 2.5 * 10^9 = 2_500000000
    const pool = makePool()
    const balanceA = 10_000000n
    const balanceB = 2_500000000n

    const plan = calculateSwapPlan(pool, -100, 100, balanceA, balanceB, DECIMALS_A, DECIMALS_B)

    expect(plan.targetAmountA).toBe(10_000000n)
    expect(plan.targetAmountB).toBe(2_500000000n)
  })

  it('target amounts calculated correctly for asymmetric ratio', () => {
    // 70/30 ratio: ratioA=0.7, ratioB=0.3
    mockCalculateDepositRatioFixTokenA.mockReturnValue({
      ratioA: { toNumber: () => 0.7 },
      ratioB: { toNumber: () => 0.3 },
    })

    // balanceA=10 USDC, balanceB=2.5 SUI (=$10) → total $20
    // targetValueA = 20 * 0.7 = 14, targetValueB = 20 * 0.3 = 6
    const pool = makePool()
    const balanceA = 10_000000n
    const balanceB = 2_500000000n

    const plan = calculateSwapPlan(pool, -100, 100, balanceA, balanceB, DECIMALS_A, DECIMALS_B)

    expect(plan.needSwap).toBe(true)
    expect(plan.a2b).toBe(false) // Need more USDC (diffA = 14-10 = +4)
    // targetAmountA = floor(14 * 10^6) = 14_000000
    expect(plan.targetAmountA).toBe(14_000000n)
    // targetAmountB = floor((6 / 4.0) * 10^9) = floor(1.5 * 10^9) = 1_500000000
    expect(plan.targetAmountB).toBe(1_500000000n)
  })

  it('swap amount is correct when needing more USDC', () => {
    // balanceA=2 USDC, balanceB=4.5 SUI (=$18) → total $20
    // 50/50 → targetA=$10, diffA=+8 → swap $8 worth of SUI = 2 SUI
    const pool = makePool()
    const balanceA = 2_000000n
    const balanceB = 4_500000000n

    const plan = calculateSwapPlan(pool, -100, 100, balanceA, balanceB, DECIMALS_A, DECIMALS_B)

    // swapSuiAmount = diffA / suiPrice = 8 / 4.0 = 2.0 SUI
    // swapRaw = floor(2.0 * 10^9) = 2_000000000
    expect(plan.swapAmount).toBe(2_000000000n)
  })

  it('swap amount is correct when needing more SUI', () => {
    // balanceA=18 USDC, balanceB=0.5 SUI (=$2) → total $20
    // 50/50 → targetA=$10, diffA=-8 → swap 8 USDC
    const pool = makePool()
    const balanceA = 18_000000n
    const balanceB = 500000000n

    const plan = calculateSwapPlan(pool, -100, 100, balanceA, balanceB, DECIMALS_A, DECIMALS_B)

    // swapUsdcAmount = |diffA| = 8 USDC
    // swapRaw = floor(8 * 10^6) = 8_000000
    expect(plan.swapAmount).toBe(8_000000n)
  })

  it('edge: both balances 0 → no swap, targets are 0', () => {
    const pool = makePool()

    const plan = calculateSwapPlan(pool, -100, 100, 0n, 0n, DECIMALS_A, DECIMALS_B)

    expect(plan.needSwap).toBe(false)
    expect(plan.swapAmount).toBe(0n)
    expect(plan.targetAmountA).toBe(0n)
    expect(plan.targetAmountB).toBe(0n)
  })
})
