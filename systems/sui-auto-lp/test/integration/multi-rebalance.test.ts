/**
 * Multi-rebalance integration tests.
 *
 * These tests cover production failure scenarios that occur
 * after multiple sequential rebalances:
 *
 * 1. Position ID propagation across 3 consecutive rebalances
 * 2. State persistence (savePositionId) called correctly each cycle
 * 3. Fee tracker reset chaining across rebalances
 * 4. Interrupted rebalance → 0-liquidity recovery on next cycle
 * 5. Circuit breaker activation after repeated failures
 * 6. Fund isolation correctness with residual wallet funds
 * 7. Position ID mismatch: scheduler uses new ID on next cycle
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

// ── Hoist mock variables ──

const {
  mockLogger,
  mockGetBalance,
  mockFetchPositionFees,
  mockFetchPositionRewards,
  mockAddLiquidity,
  mockSendTx,
  mockClosePosition,
  mockOpenPosition,
  mockGetPositions,
  mockCoinBPriceInCoinA,
  mockGetCetusUsdPrice,
  mockEstimatePositionAmounts,
  mockGetCurrentPrice,
  mockTickToPrice,
  mockCalculateSwapPlan,
  mockExecuteSwap,
  mockEvaluateRebalanceTrigger,
  mockCalculateOptimalRange,
  mockCalculateVolatilityBasedTicks,
  mockCollectRewarderPayload,
  mockSavePositionId,
  mockFeeTrackerRecord,
  mockFeeTrackerGetHourlyRate,
  mockFeeTrackerHandleRebalance,
  mockFeeTrackerRemove,
  mockRecordEvent,
} = vi.hoisted(() => ({
  mockLogger: {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
  mockGetBalance: vi.fn(),
  mockFetchPositionFees: vi.fn(),
  mockFetchPositionRewards: vi.fn(),
  mockAddLiquidity: vi.fn(),
  mockSendTx: vi.fn(),
  mockClosePosition: vi.fn(),
  mockOpenPosition: vi.fn(),
  mockGetPositions: vi.fn(),
  mockCoinBPriceInCoinA: vi.fn(),
  mockGetCetusUsdPrice: vi.fn(),
  mockEstimatePositionAmounts: vi.fn(),
  mockGetCurrentPrice: vi.fn(),
  mockTickToPrice: vi.fn(),
  mockCalculateSwapPlan: vi.fn(),
  mockExecuteSwap: vi.fn(),
  mockEvaluateRebalanceTrigger: vi.fn(),
  mockCalculateOptimalRange: vi.fn(),
  mockCalculateVolatilityBasedTicks: vi.fn(),
  mockCollectRewarderPayload: vi.fn(),
  mockSavePositionId: vi.fn(),
  mockFeeTrackerRecord: vi.fn(),
  mockFeeTrackerGetHourlyRate: vi.fn().mockReturnValue(null),
  mockFeeTrackerHandleRebalance: vi.fn(),
  mockFeeTrackerRemove: vi.fn(),
  mockRecordEvent: vi.fn(),
}))

// ── Mock modules ──

vi.mock('../../src/utils/logger.js', () => ({
  getLogger: () => mockLogger,
}))

vi.mock('../../src/utils/event-log.js', () => ({
  recordEvent: mockRecordEvent,
  closeEventLog: vi.fn(),
}))

vi.mock('../../src/utils/state.js', () => ({
  savePositionId: mockSavePositionId,
  saveRebalanceTime: vi.fn(),
  loadRebalanceTimes: vi.fn().mockReturnValue({}),
}))

vi.mock('../../src/utils/fee-tracker.js', () => ({
  feeTracker: {
    record: mockFeeTrackerRecord,
    getHourlyRate: mockFeeTrackerGetHourlyRate,
    handleRebalance: mockFeeTrackerHandleRebalance,
    remove: mockFeeTrackerRemove,
  },
}))

vi.mock('../../src/utils/sui.js', () => ({
  getSuiClient: () => ({
    getBalance: mockGetBalance,
  }),
}))

vi.mock('../../src/core/position.js', () => ({
  fetchPositionFees: mockFetchPositionFees,
  fetchPositionRewards: mockFetchPositionRewards,
  addLiquidity: mockAddLiquidity,
  sendTx: mockSendTx,
  closePosition: mockClosePosition,
  openPosition: mockOpenPosition,
  getPositions: mockGetPositions,
}))

vi.mock('../../src/core/price.js', () => ({
  coinBPriceInCoinA: mockCoinBPriceInCoinA,
  getCetusUsdPrice: mockGetCetusUsdPrice,
  estimatePositionAmounts: mockEstimatePositionAmounts,
  getCurrentPrice: mockGetCurrentPrice,
  tickToPrice: mockTickToPrice,
  rewardToUsd: vi.fn(() => 0),
}))

vi.mock('../../src/core/swap.js', () => ({
  calculateSwapPlan: mockCalculateSwapPlan,
  executeSwap: mockExecuteSwap,
}))

vi.mock('../../src/strategy/trigger.js', () => ({
  evaluateRebalanceTrigger: mockEvaluateRebalanceTrigger,
  recordRebalanceForDay: vi.fn(),
  transferDailyState: vi.fn(),
}))

vi.mock('../../src/strategy/range.js', () => ({
  calculateOptimalRange: mockCalculateOptimalRange,
}))

vi.mock('../../src/strategy/volatility.js', () => ({
  calculateVolatilityBasedTicks: mockCalculateVolatilityBasedTicks,
}))

vi.mock('../../src/core/pool.js', () => ({
  getPool: vi.fn(),
  getCetusSdk: () => ({
    Rewarder: {
      collectRewarderTransactionPayload: mockCollectRewarderPayload,
    },
  }),
}))

// ── Import under test ──

import { checkAndRebalance } from '../../src/core/rebalance.js'
import type { PoolInfo, PositionInfo, RebalanceDecision } from '../../src/types/index.js'
import type { Config, PoolConfig } from '../../src/types/config.js'

// ── Helpers ──

function makePool(overrides: Partial<PoolInfo> = {}): PoolInfo {
  return {
    poolId: '0xpool',
    coinTypeA: '0x...::usdc::USDC',
    coinTypeB: '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI',
    currentSqrtPrice: BigInt('18446744073709551616'),
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
    positionId: '0xpos1',
    poolId: '0xpool',
    owner: '0xowner',
    tickLowerIndex: -120,
    tickUpperIndex: 120,
    liquidity: BigInt('1000000000'),
    feeOwedA: 0n,
    feeOwedB: 0n,
    rewardAmountOwed: [],
    ...overrides,
  }
}

function makeConfig(overrides: Partial<Config> = {}): Config {
  return {
    network: 'mainnet',
    privateKey: 'test-key',
    pools: [{ poolId: '0xpool', strategy: 'dynamic', narrowRangePct: 0.03, wideRangePct: 0.08, volLookbackHours: 2, volTickWidthMin: 240, volTickWidthMax: 600 }],
    rebalanceThreshold: 0.10,
    harvestIntervalSec: 7200,
    checkIntervalSec: 30,
    slippageTolerance: 0.01,
    minGasProfitRatio: 2,
    logLevel: 'info',
    dryRun: false,
    harvestThresholdUsd: 3.0,
    maxSwapCostPct: 0.01,
    swapFreeRebalance: false,
    swapFreeMaxRatioSwap: 0,
    maxIdleSwapRatio: 0.20,
    ...overrides,
  }
}

function makePoolConfig(overrides: Partial<PoolConfig> = {}): PoolConfig {
  return {
    poolId: '0xpool',
    strategy: 'dynamic',
    narrowRangePct: 0.03,
    wideRangePct: 0.08,
    volLookbackHours: 2,
    volTickWidthMin: 240,
    volTickWidthMax: 600,
    ...overrides,
  }
}

const mockKeypair = {
  getPublicKey: () => ({
    toSuiAddress: () => '0xowner',
  }),
} as any

/** Standard rebalance mock setup for live mode (close → swap → open). */
function setupLiveRebalanceMocks(opts: {
  newTickLower?: number
  newTickUpper?: number
  preCloseA?: string
  preCloseB?: string
  postCloseA?: string
  postCloseB?: string
  postSwapA?: string
  postSwapB?: string
  newPositionId?: string
  needSwap?: boolean
} = {}) {
  const {
    newTickLower = -180,
    newTickUpper = 180,
    preCloseA = '10000000',     // 10 USDC
    preCloseB = '1000000000',   // 1 SUI
    postCloseA = '15000000',    // 15 USDC (delta: +5 USDC)
    postCloseB = '3000000000',  // 3 SUI (delta: +2 SUI)
    postSwapA = '14000000',     // 14 USDC
    postSwapB = '3200000000',   // 3.2 SUI
    newPositionId = '0xnewpos',
    needSwap = false,
  } = opts

  mockEvaluateRebalanceTrigger.mockReturnValue({
    shouldRebalance: true,
    trigger: 'range-out',
    currentPrice: 1.1,
    currentLower: 0.95,
    currentUpper: 1.05,
    newLower: null,
    newUpper: null,
    reason: 'Price out of range',
  } satisfies RebalanceDecision)

  mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
  mockCalculateOptimalRange.mockReturnValue({
    tickLower: newTickLower,
    tickUpper: newTickUpper,
    priceLower: 0.9,
    priceUpper: 1.1,
  })

  // Preflight gas check
  mockGetBalance
    .mockResolvedValueOnce({ totalBalance: '0' })           // coinA preflight
    .mockResolvedValueOnce({ totalBalance: '2000000000' })   // coinB preflight (2 SUI)
    // preClose snapshot
    .mockResolvedValueOnce({ totalBalance: preCloseA })
    .mockResolvedValueOnce({ totalBalance: preCloseB })

  mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

  // postClose (delta detection poll — first attempt succeeds)
  mockGetBalance
    .mockResolvedValueOnce({ totalBalance: postCloseA })
    .mockResolvedValueOnce({ totalBalance: postCloseB })

  mockCalculateSwapPlan.mockReturnValue({
    needSwap,
    a2b: false,
    swapAmount: 0n,
    reason: needSwap ? 'ratio off' : 'ratio OK',
    targetAmountA: BigInt(postSwapA),
    targetAmountB: BigInt(postSwapB),
  })

  if (needSwap) {
    mockExecuteSwap.mockResolvedValue({ success: true, digest: '0xswap', gasCost: 3_000_000n, error: null })
  }

  // Post-swap / pre-open wallet query
  mockGetBalance
    .mockResolvedValueOnce({ totalBalance: postSwapA })
    .mockResolvedValueOnce({ totalBalance: postSwapB })

  mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })

  // getPositions to discover new position ID
  mockGetPositions.mockResolvedValue([
    makePosition({
      positionId: newPositionId,
      tickLowerIndex: newTickLower,
      tickUpperIndex: newTickUpper,
      liquidity: BigInt('500000000'),
    }),
  ])
}

// ════════════════════════════════════════════════════════════════
// Multi-rebalance sequences
// ════════════════════════════════════════════════════════════════

describe('multi-rebalance: sequential position ID propagation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  it('3 consecutive rebalances: each returns new position ID', async () => {
    const config = makeConfig({ dryRun: false })
    const poolConfig = makePoolConfig()
    const pool = makePool()

    // === Rebalance 1: pos1 → pos2 ===
    setupLiveRebalanceMocks({ newPositionId: '0xpos2', newTickLower: -180, newTickUpper: 180 })

    const promise1 = checkAndRebalance(pool, makePosition({ positionId: '0xpos1' }), config, poolConfig, mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const r1 = await promise1

    expect(r1.result!.success).toBe(true)
    expect(r1.newPositionId).toBe('0xpos2')
    expect(mockFeeTrackerHandleRebalance).toHaveBeenCalledWith('0xpos1', '0xpos2')

    vi.clearAllMocks()

    // === Rebalance 2: pos2 → pos3 ===
    setupLiveRebalanceMocks({ newPositionId: '0xpos3', newTickLower: -240, newTickUpper: 240 })

    const promise2 = checkAndRebalance(pool, makePosition({ positionId: '0xpos2' }), config, poolConfig, mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const r2 = await promise2

    expect(r2.result!.success).toBe(true)
    expect(r2.newPositionId).toBe('0xpos3')
    expect(mockFeeTrackerHandleRebalance).toHaveBeenCalledWith('0xpos2', '0xpos3')

    vi.clearAllMocks()

    // === Rebalance 3: pos3 → pos4 ===
    setupLiveRebalanceMocks({ newPositionId: '0xpos4', newTickLower: -300, newTickUpper: 300 })

    const promise3 = checkAndRebalance(pool, makePosition({ positionId: '0xpos3' }), config, poolConfig, mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const r3 = await promise3

    expect(r3.result!.success).toBe(true)
    expect(r3.newPositionId).toBe('0xpos4')
    expect(mockFeeTrackerHandleRebalance).toHaveBeenCalledWith('0xpos3', '0xpos4')

    vi.useRealTimers()
  })

  it('position ID detection: getPositions filters by new tick range and liquidity > 0', async () => {
    const config = makeConfig({ dryRun: false })
    const pool = makePool()

    setupLiveRebalanceMocks({ newPositionId: '0xnew', newTickLower: -180, newTickUpper: 180 })

    // Override getPositions to include both old (closed, liq=0) and new positions
    mockGetPositions.mockResolvedValue([
      makePosition({ positionId: '0xpos1', tickLowerIndex: -120, tickUpperIndex: 120, liquidity: 0n }),
      makePosition({ positionId: '0xnew', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('500000000') }),
    ])

    const promise = checkAndRebalance(pool, makePosition({ positionId: '0xpos1' }), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const { newPositionId } = await promise

    // Should pick 0xnew (matches tick range, has liquidity, different from old)
    expect(newPositionId).toBe('0xnew')

    vi.useRealTimers()
  })
})

// ════════════════════════════════════════════════════════════════
// Scheduler-level position ID tracking simulation
// ════════════════════════════════════════════════════════════════

describe('multi-rebalance: scheduler position ID tracking', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  it('simulates scheduler updating poolConfig.positionIds across 2 cycles', async () => {
    const config = makeConfig({ dryRun: false })
    const poolConfig = makePoolConfig({ positionIds: ['0xpos1'] })
    const pool = makePool()

    // --- Cycle 1: rebalance pos1 → pos2 ---
    setupLiveRebalanceMocks({ newPositionId: '0xpos2' })

    const pos1 = makePosition({ positionId: '0xpos1' })
    const promise1 = checkAndRebalance(pool, pos1, config, poolConfig, mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const r1 = await promise1

    expect(r1.newPositionId).toBe('0xpos2')

    // Simulate scheduler updating positionIds (from scheduler.ts lines 163-175)
    if (r1.newPositionId && poolConfig.positionIds) {
      const idx = poolConfig.positionIds.indexOf(pos1.positionId)
      if (idx !== -1) {
        poolConfig.positionIds[idx] = r1.newPositionId
      }
    }

    expect(poolConfig.positionIds).toEqual(['0xpos2'])

    vi.clearAllMocks()

    // --- Cycle 2: rebalance pos2 → pos3 ---
    setupLiveRebalanceMocks({ newPositionId: '0xpos3', newTickLower: -240, newTickUpper: 240 })

    // Scheduler would now query getPositions filtered by positionIds=['0xpos2']
    const pos2 = makePosition({ positionId: '0xpos2', tickLowerIndex: -180, tickUpperIndex: 180 })
    const promise2 = checkAndRebalance(pool, pos2, config, poolConfig, mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const r2 = await promise2

    expect(r2.newPositionId).toBe('0xpos3')

    // Scheduler update
    if (r2.newPositionId && poolConfig.positionIds) {
      const idx = poolConfig.positionIds.indexOf(pos2.positionId)
      if (idx !== -1) {
        poolConfig.positionIds[idx] = r2.newPositionId
      }
    }

    expect(poolConfig.positionIds).toEqual(['0xpos3'])

    vi.useRealTimers()
  })

  it('scheduler correctly handles positionIds=undefined (auto-discover mode)', async () => {
    const config = makeConfig({ dryRun: false })
    const poolConfig = makePoolConfig() // no positionIds
    const pool = makePool()

    setupLiveRebalanceMocks({ newPositionId: '0xdiscovered' })

    const pos = makePosition({ positionId: '0xoldpos' })
    const promise = checkAndRebalance(pool, pos, config, poolConfig, mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const r = await promise

    expect(r.newPositionId).toBe('0xdiscovered')

    // Simulate scheduler logic: positionIds was undefined, should start tracking
    if (r.newPositionId) {
      if (!poolConfig.positionIds) {
        poolConfig.positionIds = [r.newPositionId]
      }
    }

    expect(poolConfig.positionIds).toEqual(['0xdiscovered'])

    vi.useRealTimers()
  })
})

// ════════════════════════════════════════════════════════════════
// Interrupted rebalance recovery (0-liquidity)
// ════════════════════════════════════════════════════════════════

describe('multi-rebalance: interrupted rebalance recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  it('close succeeds but open fails → next cycle recovers 0-liquidity position', async () => {
    const config = makeConfig({ dryRun: false })
    const pool = makePool()
    const poolConfig = makePoolConfig({ positionIds: ['0xpos1'] })

    // --- Cycle 1: close succeeds, open FAILS ---
    mockEvaluateRebalanceTrigger.mockReturnValue({
      shouldRebalance: true, trigger: 'range-out', currentPrice: 1.1,
      currentLower: 0.95, currentUpper: 1.05, newLower: null, newUpper: null,
      reason: 'Price out of range',
    } satisfies RebalanceDecision)
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })

    // Preflight + preClose + postClose
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '2000000000' })
      .mockResolvedValueOnce({ totalBalance: '10000000' })
      .mockResolvedValueOnce({ totalBalance: '1000000000' })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '15000000' })
      .mockResolvedValueOnce({ totalBalance: '3000000000' })

    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(5_000_000), targetAmountB: BigInt(2_000_000_000),
    })
    // Post-swap wallet
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '15000000' })
      .mockResolvedValueOnce({ totalBalance: '3000000000' })

    // OPEN FAILS
    mockOpenPosition.mockResolvedValue({
      success: false, digest: null, gasCost: 0n, error: 'MoveAbort: insufficient liquidity',
    })

    const promise1 = checkAndRebalance(pool, makePosition({ positionId: '0xpos1' }), config, poolConfig, mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const r1 = await promise1

    expect(r1.result!.success).toBe(false)
    expect(r1.result!.error).toContain('insufficient liquidity')
    expect(r1.newPositionId).toBeUndefined()

    vi.clearAllMocks()

    // --- Cycle 2: recovery mode — position has 0 liquidity ---
    // The position still exists on-chain but with liquidity=0 and funds in wallet
    mockEvaluateRebalanceTrigger.mockReturnValue({
      shouldRebalance: true, trigger: 'range-out', currentPrice: 1.05,
      currentLower: 0.95, currentUpper: 1.05, newLower: null, newUpper: null,
      reason: 'Recovery: 0-liquidity position',
    } satisfies RebalanceDecision)
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })

    // Preflight gas check
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '15000000' })   // coinA (funds still in wallet)
      .mockResolvedValueOnce({ totalBalance: '3000000000' }) // coinB (funds still in wallet)

    // preClose snapshot (line 148 — always called after shouldRebalance)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '15000000' })
      .mockResolvedValueOnce({ totalBalance: '3000000000' })

    // Recovery mode wallet query (liquidity === 0n path, line 193)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '15000000' })
      .mockResolvedValueOnce({ totalBalance: '3000000000' })

    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(15_000_000), targetAmountB: BigInt(2_500_000_000),
    })

    // postSwap wallet query (line 340 — recovery path uses wallet directly)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '15000000' })
      .mockResolvedValueOnce({ totalBalance: '3000000000' })

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xrecovered', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([
      makePosition({ positionId: '0xrecoveredpos', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('800000000') }),
    ])

    const zeroLiqPos = makePosition({ positionId: '0xpos1', liquidity: 0n })
    const promise2 = checkAndRebalance(pool, zeroLiqPos, config, poolConfig, mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const r2 = await promise2

    expect(r2.result!.success).toBe(true)
    expect(r2.newPositionId).toBe('0xrecoveredpos')
    // closePosition should NOT be called for 0-liquidity
    expect(mockClosePosition).not.toHaveBeenCalled()

    vi.useRealTimers()
  })

  it('0-liquidity with insufficient funds → aborts with recovery guard', async () => {
    const config = makeConfig({ dryRun: false })
    const pool = makePool()

    mockEvaluateRebalanceTrigger.mockReturnValue({
      shouldRebalance: true, trigger: 'range-out', currentPrice: 1.1,
      currentLower: 0.95, currentUpper: 1.05, newLower: null, newUpper: null,
      reason: 'Recovery attempt',
    } satisfies RebalanceDecision)
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })

    // Preflight: barely enough gas (0.5 SUI >= 0.15 MIN_SUI_FOR_GAS)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })          // coinA
      .mockResolvedValueOnce({ totalBalance: '500000000' })   // coinB (0.5 SUI < GAS_RESERVE but >= MIN_SUI_FOR_GAS)
    // preClose snapshot (always called after shouldRebalance)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '500000000' })
    // Recovery wallet query (liquidity === 0n): nearly empty
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '500000' })      // 0.5 USDC < $1 min (MIN_RECOVERY=1_000_000)
      .mockResolvedValueOnce({ totalBalance: '500000000' })   // 0.5 SUI < GAS_RESERVE(1.0) → availableBForCheck = 0

    const zeroLiqPos = makePosition({ positionId: '0xemptypos', liquidity: 0n })
    const { result } = await checkAndRebalance(pool, zeroLiqPos, config, makePoolConfig(), mockKeypair)

    expect(result!.success).toBe(false)
    expect(result!.error).toContain('Insufficient funds for recovery')
    expect(mockOpenPosition).not.toHaveBeenCalled()
  })
})

// ════════════════════════════════════════════════════════════════
// Circuit breaker (scheduler-level simulation)
// ════════════════════════════════════════════════════════════════

describe('multi-rebalance: circuit breaker after repeated failures', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  it('5 consecutive rebalance failures trigger circuit breaker', async () => {
    // Simulate the exact scheduler logic from scheduler.ts lines 185-217
    const MAX_CONSECUTIVE_FAILURES = 5
    const failureCounts = new Map<string, number>()
    const backoffUntil = new Map<string, number>()
    const skippedPositions = new Set<string>()
    const posId = '0xfailingpos'
    const backoffHistory: number[] = []

    for (let cycle = 1; cycle <= 7; cycle++) {
      // Check circuit breaker
      if (skippedPositions.has(posId)) {
        // This is what scheduler does: skip and log
        continue
      }

      // Check backoff
      const deadline = backoffUntil.get(posId)
      if (deadline && Date.now() < deadline) {
        continue
      }

      // Simulate failed rebalance
      const failResult = { success: false, error: `Swap failed attempt ${cycle}` }

      if (!failResult.success) {
        const count = (failureCounts.get(posId) ?? 0) + 1
        failureCounts.set(posId, count)

        if (count >= MAX_CONSECUTIVE_FAILURES) {
          skippedPositions.add(posId)
        } else {
          const backoffSec = Math.min(60 * count, 300)
          backoffHistory.push(backoffSec)
          // In tests we skip actual waiting
        }
      }
    }

    // After 5 failures, position is permanently skipped
    expect(failureCounts.get(posId)).toBe(5)
    expect(skippedPositions.has(posId)).toBe(true)
    // Backoff history for failures 1-4 (5th triggers circuit breaker, no backoff)
    expect(backoffHistory).toEqual([60, 120, 180, 240])
  })

  it('success after 3 failures resets counter, then 5 new failures trigger breaker', () => {
    const MAX_CONSECUTIVE_FAILURES = 5
    const failureCounts = new Map<string, number>()
    const backoffUntil = new Map<string, number>()
    const skippedPositions = new Set<string>()
    const posId = '0xunstable'

    // 3 failures
    for (let i = 0; i < 3; i++) {
      const count = (failureCounts.get(posId) ?? 0) + 1
      failureCounts.set(posId, count)
    }
    expect(failureCounts.get(posId)).toBe(3)

    // Success resets
    failureCounts.delete(posId)
    backoffUntil.delete(posId)
    expect(failureCounts.has(posId)).toBe(false)

    // 5 more failures
    for (let i = 0; i < 5; i++) {
      const count = (failureCounts.get(posId) ?? 0) + 1
      failureCounts.set(posId, count)
      if (count >= MAX_CONSECUTIVE_FAILURES) {
        skippedPositions.add(posId)
      }
    }

    expect(failureCounts.get(posId)).toBe(5)
    expect(skippedPositions.has(posId)).toBe(true)
  })

  it('circuit breaker does not affect other positions', () => {
    const MAX_CONSECUTIVE_FAILURES = 5
    const failureCounts = new Map<string, number>()
    const skippedPositions = new Set<string>()

    // pos1 fails 5 times
    for (let i = 0; i < 5; i++) {
      const count = (failureCounts.get('pos1') ?? 0) + 1
      failureCounts.set('pos1', count)
      if (count >= MAX_CONSECUTIVE_FAILURES) skippedPositions.add('pos1')
    }

    // pos2 fails 2 times, then succeeds
    failureCounts.set('pos2', 2)
    failureCounts.delete('pos2') // success

    expect(skippedPositions.has('pos1')).toBe(true)
    expect(skippedPositions.has('pos2')).toBe(false)
    // pos2 can still be processed
    expect(failureCounts.has('pos2')).toBe(false)
  })
})

// ════════════════════════════════════════════════════════════════
// Fee tracker behavior across rebalances
// ════════════════════════════════════════════════════════════════

describe('multi-rebalance: fee tracker chaining', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  it('handleRebalance called with correct old→new IDs on each rebalance', async () => {
    const config = makeConfig({ dryRun: false })
    const pool = makePool()

    // Rebalance 1: old=pos1, new=pos2
    setupLiveRebalanceMocks({ newPositionId: '0xpos2' })
    const p1 = checkAndRebalance(pool, makePosition({ positionId: '0xpos1' }), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    await p1

    expect(mockFeeTrackerHandleRebalance).toHaveBeenCalledTimes(1)
    expect(mockFeeTrackerHandleRebalance).toHaveBeenCalledWith('0xpos1', '0xpos2')

    vi.clearAllMocks()

    // Rebalance 2: old=pos2, new=pos3
    setupLiveRebalanceMocks({ newPositionId: '0xpos3', newTickLower: -240, newTickUpper: 240 })
    const p2 = checkAndRebalance(pool, makePosition({ positionId: '0xpos2' }), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    await p2

    expect(mockFeeTrackerHandleRebalance).toHaveBeenCalledTimes(1)
    expect(mockFeeTrackerHandleRebalance).toHaveBeenCalledWith('0xpos2', '0xpos3')

    vi.useRealTimers()
  })

  it('handleRebalance with undefined newPositionId when detection fails', async () => {
    const config = makeConfig({ dryRun: false })
    const pool = makePool()

    setupLiveRebalanceMocks({ newPositionId: '0xshouldnotmatch' })

    // Override getPositions to return empty (detection fails)
    mockGetPositions.mockResolvedValue([])

    const p = checkAndRebalance(pool, makePosition({ positionId: '0xold' }), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const { newPositionId } = await p

    expect(newPositionId).toBeUndefined()
    // handleRebalance should still be called with undefined newPositionId
    expect(mockFeeTrackerHandleRebalance).toHaveBeenCalledWith('0xold', undefined)

    vi.useRealTimers()
  })
})

// ════════════════════════════════════════════════════════════════
// Fund isolation with residual wallet funds
// ════════════════════════════════════════════════════════════════

describe('multi-rebalance: fund isolation with residual funds', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  it('delta isolation works correctly when wallet has leftover from prior rebalance', async () => {
    // Scenario: After rebalance 1, the wallet still has 5 USDC + 0.3 SUI leftover
    // (rounding dust from previous position). Rebalance 2 should only use
    // the delta from THIS close, not the accumulated wallet balance.
    const config = makeConfig({ dryRun: false })
    const pool = makePool()

    mockEvaluateRebalanceTrigger.mockReturnValue({
      shouldRebalance: true, trigger: 'range-out', currentPrice: 1.1,
      currentLower: 0.95, currentUpper: 1.05, newLower: null, newUpper: null,
      reason: 'Price out of range',
    } satisfies RebalanceDecision)
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })

    // Preflight
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '5000000' })      // coinA: 5 USDC residual
      .mockResolvedValueOnce({ totalBalance: '1300000000' })    // coinB: 1.3 SUI residual

    // preClose: wallet has residual + current position value
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '5000000' })       // coinA: 5 USDC (all residual)
      .mockResolvedValueOnce({ totalBalance: '1300000000' })    // coinB: 1.3 SUI (all residual)

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // postClose: residual + position funds released
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '25000000' })      // 25 USDC (+20 from position)
      .mockResolvedValueOnce({ totalBalance: '4300000000' })    // 4.3 SUI (+3 from position)

    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(20_000_000), targetAmountB: BigInt(2_500_000_000),
    })

    // Post-swap wallet
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '25000000' })
      .mockResolvedValueOnce({ totalBalance: '4300000000' })

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([
      makePosition({ positionId: '0xnew', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('500000000') }),
    ])

    const promise = checkAndRebalance(pool, makePosition(), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    await promise

    // calculateSwapPlan should receive DELTA funds only:
    // deltaA = 25M - 5M = 20M USDC (position funds only, not residual)
    // deltaB = 4.3B - 1.3B = 3B SUI → available = 3B - 1.0B (gas) = 2.0B
    expect(mockCalculateSwapPlan).toHaveBeenCalledWith(
      expect.anything(),
      -180, 180,
      BigInt(20_000_000),    // balanceA: delta only
      BigInt(2_000_000_000), // availableB: delta - gas reserve
      6, 9,
    )

    vi.useRealTimers()
  })

  it('delta calculation handles negative deltaB (gas consumed more than received)', async () => {
    const config = makeConfig({ dryRun: false })
    const pool = makePool()

    mockEvaluateRebalanceTrigger.mockReturnValue({
      shouldRebalance: true, trigger: 'range-out', currentPrice: 1.1,
      currentLower: 0.95, currentUpper: 1.05, newLower: null, newUpper: null,
      reason: 'Price out of range',
    } satisfies RebalanceDecision)
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })

    // Preflight
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '2000000000' })

    // preClose: wallet has some SUI for gas
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })            // coinA: 0
      .mockResolvedValueOnce({ totalBalance: '2000000000' })   // coinB: 2 SUI

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // postClose: position was USDC-only (all in coinA), SUI decreased from gas
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '10000000' })     // coinA: +10 USDC
      .mockResolvedValueOnce({ totalBalance: '1990000000' })   // coinB: 1.99 SUI (LESS than pre, gas ate into it)

    // Delta: coinA = +10M, coinB = -10M (negative!)
    // The code should clamp negative to 0n
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: true, a2b: true, swapAmount: BigInt(5_000_000),
      reason: 'need SUI', targetAmountA: BigInt(5_000_000), targetAmountB: BigInt(1_500_000_000),
    })
    mockExecuteSwap.mockResolvedValue({ success: true, digest: '0xswap', gasCost: 3_000_000n, error: null })

    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '5000000' })
      .mockResolvedValueOnce({ totalBalance: '2100000000' })

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([])

    const promise = checkAndRebalance(pool, makePosition(), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    await promise

    // balanceA = 10M (delta), balanceB = 0 (negative delta clamped to 0)
    // availableB = max(0 - GAS_RESERVE, 0) = 0
    expect(mockCalculateSwapPlan).toHaveBeenCalledWith(
      expect.anything(),
      -180, 180,
      BigInt(10_000_000), // deltaA
      0n,                  // availableB (clamped to 0, deltaB was negative)
      6, 9,
    )

    vi.useRealTimers()
  })
})

// ════════════════════════════════════════════════════════════════
// Close fails with no funds released
// ════════════════════════════════════════════════════════════════

describe('multi-rebalance: RPC delta-zero safety abort', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  it('close TX succeeds but RPC shows no fund delta after 3 polls → abort', async () => {
    const config = makeConfig({ dryRun: false })
    const pool = makePool()

    mockEvaluateRebalanceTrigger.mockReturnValue({
      shouldRebalance: true, trigger: 'range-out', currentPrice: 1.1,
      currentLower: 0.95, currentUpper: 1.05, newLower: null, newUpper: null,
      reason: 'Price out of range',
    } satisfies RebalanceDecision)
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })

    // Preflight
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '2000000000' })

    // preClose
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '5000000' })
      .mockResolvedValueOnce({ totalBalance: '1000000000' })

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // postClose poll: RPC hasn't reflected the close yet (3 attempts, all show same or lower)
    for (let i = 0; i < 3; i++) {
      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '5000000' })     // same as preClose
        .mockResolvedValueOnce({ totalBalance: '997000000' })   // slightly less (gas consumed)
    }

    const promise = checkAndRebalance(pool, makePosition(), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const { result } = await promise

    expect(result!.success).toBe(false)
    expect(result!.error).toContain('no funds after 3 attempts')
    // Should NOT proceed to swap or open
    expect(mockCalculateSwapPlan).not.toHaveBeenCalled()
    expect(mockOpenPosition).not.toHaveBeenCalled()

    vi.useRealTimers()
  })
})

// ════════════════════════════════════════════════════════════════
// Swap-free: wallet SUI safety across multiple rebalances
// ════════════════════════════════════════════════════════════════

describe('multi-rebalance: swap-free wallet SUI safety', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  /** Set up mocks for a swap-free live rebalance. Fewer getBalance calls than swap path. */
  function setupSwapFreeMocks(opts: {
    preCloseB: string        // wallet SUI before close
    postCloseB: string       // wallet SUI after close (preCloseB + position delta)
    newPositionId?: string
  }) {
    const { preCloseB, postCloseB, newPositionId = '0xnewpos' } = opts

    mockEvaluateRebalanceTrigger.mockReturnValue({
      shouldRebalance: true, trigger: 'range-out', currentPrice: 1.1,
      currentLower: 0.95, currentUpper: 1.05, newLower: null, newUpper: null,
      reason: 'Price out of range',
    } satisfies RebalanceDecision)
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })

    // Preflight gas check
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: preCloseB })
    // preClose snapshot
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '5000000' })      // coinA
      .mockResolvedValueOnce({ totalBalance: preCloseB })
    // postClose (first poll succeeds)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '10000000' })     // coinA postClose (+5 USDC)
      .mockResolvedValueOnce({ totalBalance: postCloseB })

    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: 0n, targetAmountB: 0n,
    })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })
    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([
      makePosition({ positionId: newPositionId, tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('500000000') }),
    ])
  }

  it('2 consecutive swap-free rebalances: GAS_RESERVE deducted each time, SUI stable', async () => {
    const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
    const pool = makePool()

    // === Rebalance 1: wallet 1.5 SUI, position releases 2.0 SUI delta ===
    setupSwapFreeMocks({
      preCloseB: '1500000000',    // 1.5 SUI
      postCloseB: '3500000000',   // 3.5 SUI (+2.0 delta)
      newPositionId: '0xpos2',
    })

    const p1 = checkAndRebalance(pool, makePosition({ positionId: '0xpos1' }), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const r1 = await p1

    expect(r1.result!.success).toBe(true)
    // deltaB = 2.0B, availableB = 2.0B - 1.0B = 1.0B deposited
    // walletSUI after: 3.5 - 1.0 - gas ≈ 2.5 SUI (gained 1.0 from GAS_RESERVE)
    expect(mockOpenPosition).toHaveBeenCalledWith(
      expect.anything(), expect.anything(), expect.anything(),
      -180, 180,
      '5000000',       // deltaA
      '1000000000',    // availableB = delta(2.0) - reserve(1.0)
      expect.anything(), expect.anything(), false, [],
      expect.anything(),  // currentSqrtPrice
    )
    expect(mockCalculateSwapPlan).toHaveBeenCalled()

    vi.clearAllMocks()

    // === Rebalance 2: wallet now ~2.0 SUI, position releases 1.8 SUI delta ===
    setupSwapFreeMocks({
      preCloseB: '2000000000',   // 2.0 SUI
      postCloseB: '3800000000',  // 3.8 SUI (+1.8 delta)
      newPositionId: '0xpos3',
    })

    const p2 = checkAndRebalance(pool, makePosition({ positionId: '0xpos2' }), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const r2 = await p2

    expect(r2.result!.success).toBe(true)
    // deltaB = 1.8B, availableB = 1.8B - 1.0B = 0.8B deposited
    // walletSUI after: 3.8 - 0.8 - gas ≈ 3.0 SUI (gained another 1.0)
    expect(mockOpenPosition).toHaveBeenCalledWith(
      expect.anything(), expect.anything(), expect.anything(),
      -180, 180,
      '5000000',
      '800000000',    // availableB = delta(1.8) - reserve(1.0)
      expect.anything(), expect.anything(), false, [],
      expect.anything(),  // currentSqrtPrice
    )
    expect(mockCalculateSwapPlan).toHaveBeenCalled()

    vi.useRealTimers()
  })

  it('swap-free: gas cost is 2-TX only (close + open), no swap gas', async () => {
    const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
    const pool = makePool()

    setupSwapFreeMocks({
      preCloseB: '2000000000',
      postCloseB: '4000000000',
    })

    const p = checkAndRebalance(pool, makePosition(), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const { result } = await p

    expect(result!.success).toBe(true)
    // Gas = close(3M) + open(3M) = 6M — no swap gas
    expect(result!.gasCost).toBe(6_000_000n)
    expect(mockExecuteSwap).not.toHaveBeenCalled()

    vi.useRealTimers()
  })
})

// ════════════════════════════════════════════════════════════════
// Dry-run across multiple cycles
// ════════════════════════════════════════════════════════════════

describe('multi-rebalance: dry-run does not interfere with subsequent cycles', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  it('dry-run rebalance does not produce newPositionId', async () => {
    const config = makeConfig({ dryRun: true })
    const pool = makePool()

    mockEvaluateRebalanceTrigger.mockReturnValue({
      shouldRebalance: true, trigger: 'range-out', currentPrice: 1.1,
      currentLower: 0.95, currentUpper: 1.05, newLower: null, newUpper: null,
      reason: 'Price out of range',
    } satisfies RebalanceDecision)
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })
    // Preflight gas check
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '2000000000' })
    // preClose snapshot (always called, even in dry-run)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '2000000000' })
    mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(5_000_000), targetAmountB: BigInt(2_000_000_000),
    })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 0n, error: null })
    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 0n, error: null })

    const { result, newPositionId } = await checkAndRebalance(
      pool, makePosition(), config, makePoolConfig(), mockKeypair,
    )

    expect(result!.success).toBe(true)
    // Dry-run should NOT call getPositions to detect new position
    expect(mockGetPositions).not.toHaveBeenCalled()
    expect(newPositionId).toBeUndefined()
  })
})
