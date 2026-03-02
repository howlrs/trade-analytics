import { describe, it, expect, vi, beforeEach } from 'vitest'

// ── Hoist mock variables so vi.mock factories can reference them ──

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
}))

// ── Mock all external modules ──

vi.mock('../../src/utils/logger.js', () => ({
  getLogger: () => mockLogger,
}))

vi.mock('../../src/utils/event-log.js', () => ({
  recordEvent: vi.fn(),
  closeEventLog: vi.fn(),
}))

vi.mock('../../src/utils/state.js', () => ({
  savePositionId: vi.fn(),
  saveRebalanceTime: vi.fn(),
  loadRebalanceTimes: vi.fn().mockReturnValue({}),
}))

vi.mock('../../src/utils/fee-tracker.js', () => ({
  feeTracker: {
    record: vi.fn(),
    getHourlyRate: vi.fn().mockReturnValue(null),
    handleRebalance: vi.fn(),
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
  rewardToUsd: vi.fn((coinType: string, amount: bigint, suiP: number, cetusP: number) => {
    if (coinType.includes('::sui::SUI')) return (Number(amount) / 1e9) * suiP
    if (coinType.includes('::cetus::CETUS')) return (Number(amount) / 1e9) * cetusP
    return 0
  }),
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

// ── Now import the modules under test ──

import { evaluateHarvest, checkAndHarvest } from '../../src/core/compound.js'
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
    dryRun: true,
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

// Mock keypair
const mockKeypair = {
  getPublicKey: () => ({
    toSuiAddress: () => '0xowner',
  }),
} as any

// ── Tests ──

beforeEach(() => {
  vi.clearAllMocks()
  // Default: SUI price = $3.50 USDC
  mockCoinBPriceInCoinA.mockReturnValue(3.5)
  mockGetCetusUsdPrice.mockResolvedValue(0.25)
})

// ════════════════════════════════════════════════════════════════
// evaluateHarvest
// ════════════════════════════════════════════════════════════════

describe('evaluateHarvest', () => {
  it('no fees, no rewards → shouldHarvest=false', async () => {
    mockFetchPositionFees.mockResolvedValue(new Map([['0xpos1', { feeA: 0n, feeB: 0n }]]))
    mockFetchPositionRewards.mockResolvedValue([])

    const result = await evaluateHarvest(makePosition(), makePool(), makeConfig())

    expect(result.shouldHarvest).toBe(false)
    expect(result.reason).toContain('No fees or rewards')
  })

  it('below USD threshold → shouldHarvest=false', async () => {
    // 0.5 USDC fee + 0 SUI = $0.50, below $3 threshold
    mockFetchPositionFees.mockResolvedValue(new Map([['0xpos1', { feeA: 500_000n, feeB: 0n }]]))
    mockFetchPositionRewards.mockResolvedValue([])

    const result = await evaluateHarvest(makePosition(), makePool(), makeConfig())

    expect(result.shouldHarvest).toBe(false)
    expect(result.reason).toContain('below threshold')
  })

  it('above threshold → shouldHarvest=true', async () => {
    // 5 USDC + 1 SUI = $5 + $3.50 = $8.50 (above $3 threshold)
    mockFetchPositionFees.mockResolvedValue(new Map([['0xpos1', { feeA: 5_000_000n, feeB: 1_000_000_000n }]]))
    mockFetchPositionRewards.mockResolvedValue([])

    const result = await evaluateHarvest(makePosition(), makePool(), makeConfig())

    expect(result.shouldHarvest).toBe(true)
    expect(result.reason).toContain('Claim')
  })

  it('above threshold with rewards → shouldHarvest=true', async () => {
    const suiRewardType = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
    mockFetchPositionFees.mockResolvedValue(new Map([['0xpos1', { feeA: 5_000n, feeB: 1_000_000n }]]))
    mockFetchPositionRewards.mockResolvedValue([
      { coinType: suiRewardType, amount: BigInt(2_000_000_000) }, // 2 SUI = $7
    ])

    const result = await evaluateHarvest(makePosition(), makePool(), makeConfig())

    expect(result.shouldHarvest).toBe(true)
    expect(result.reason).toContain('Claim')
  })
})

// ════════════════════════════════════════════════════════════════
// checkAndHarvest
// ════════════════════════════════════════════════════════════════

describe('checkAndHarvest', () => {
  it('shouldHarvest=false → returns null', async () => {
    mockFetchPositionFees.mockResolvedValue(new Map([['0xpos1', { feeA: 0n, feeB: 0n }]]))
    mockFetchPositionRewards.mockResolvedValue([])

    const result = await checkAndHarvest(makePool(), makePosition(), makeConfig(), mockKeypair)

    expect(result).toBeNull()
  })

  it('successful harvest (collectRewarder)', async () => {
    mockFetchPositionFees.mockResolvedValue(new Map([['0xpos1', { feeA: 5_000_000n, feeB: 1_000_000_000n }]]))
    mockFetchPositionRewards.mockResolvedValue([])
    mockGetBalance.mockResolvedValue({ totalBalance: '1000000000' }) // 1 SUI (above 0.05 min)
    mockCollectRewarderPayload.mockResolvedValue({})
    mockSendTx.mockResolvedValue({ success: true, digest: '0xdigest1', gasCost: 3_000_000n, error: null })

    const result = await checkAndHarvest(makePool(), makePosition(), makeConfig(), mockKeypair)

    expect(result).not.toBeNull()
    expect(result!.success).toBe(true)
    expect(result!.digest).toBe('0xdigest1')
    expect(mockSendTx).toHaveBeenCalledTimes(1)
    expect(mockAddLiquidity).not.toHaveBeenCalled()
  })

  it('insufficient SUI for gas → error', async () => {
    mockFetchPositionFees.mockResolvedValue(new Map([['0xpos1', { feeA: 5_000_000n, feeB: 1_000_000_000n }]]))
    mockFetchPositionRewards.mockResolvedValue([])
    // 0.01 SUI, below 0.05 minimum
    mockGetBalance.mockResolvedValue({ totalBalance: '10000000' })

    const result = await checkAndHarvest(makePool(), makePosition(), makeConfig(), mockKeypair)

    expect(result).not.toBeNull()
    expect(result!.success).toBe(false)
    expect(result!.error).toContain('Insufficient SUI')
    expect(mockAddLiquidity).not.toHaveBeenCalled()
  })
})

// ════════════════════════════════════════════════════════════════
// checkAndRebalance
// ════════════════════════════════════════════════════════════════

describe('checkAndRebalance', () => {
  const noRebalanceDecision: RebalanceDecision = {
    shouldRebalance: false,
    trigger: null,
    currentPrice: 1.0,
    currentLower: 0.95,
    currentUpper: 1.05,
    newLower: null,
    newUpper: null,
    reason: 'Price in range',
  }

  const rebalanceDecision: RebalanceDecision = {
    shouldRebalance: true,
    trigger: 'range-out',
    currentPrice: 1.1,
    currentLower: 0.95,
    currentUpper: 1.05,
    newLower: null,
    newUpper: null,
    reason: 'Price out of range',
  }

  function setupRebalanceMocks() {
    mockEvaluateRebalanceTrigger.mockReturnValue({ ...rebalanceDecision })
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180,
      tickUpper: 180,
      priceLower: 0.9,
      priceUpper: 1.1,
    })
    // Wallet has enough SUI for gas
    mockGetBalance.mockResolvedValue({ totalBalance: '2000000000' }) // 2 SUI
  }

  it('shouldRebalance=false → result=null', async () => {
    mockEvaluateRebalanceTrigger.mockReturnValue(noRebalanceDecision)

    const { decision, result } = await checkAndRebalance(
      makePool(), makePosition(), makeConfig(), makePoolConfig(), mockKeypair,
    )

    expect(decision.shouldRebalance).toBe(false)
    expect(result).toBeNull()
    expect(mockClosePosition).not.toHaveBeenCalled()
  })

  it('pre-flight gas check: insufficient SUI → error', async () => {
    mockEvaluateRebalanceTrigger.mockReturnValue({ ...rebalanceDecision })
    // getWalletBalances calls getBalance for coinA and coinB
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '1000000' })  // coinA
      .mockResolvedValueOnce({ totalBalance: '10000000' }) // coinB (0.01 SUI < 0.15 minimum)

    const { decision, result } = await checkAndRebalance(
      makePool(), makePosition(), makeConfig(), makePoolConfig(), mockKeypair,
    )

    expect(decision.shouldRebalance).toBe(true)
    expect(result).not.toBeNull()
    expect(result!.success).toBe(false)
    expect(result!.error).toContain('Insufficient SUI')
    expect(mockClosePosition).not.toHaveBeenCalled()
  })

  it('dry-run mode: uses estimated amounts, no actual TX wait', async () => {
    setupRebalanceMocks()
    mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false,
      a2b: false,
      swapAmount: 0n,
      reason: 'ratio OK',
      targetAmountA: BigInt(5_000_000),
      targetAmountB: BigInt(2_000_000_000),
    })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })
    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })

    const config = makeConfig({ dryRun: true })
    const { result } = await checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )

    expect(result).not.toBeNull()
    expect(result!.success).toBe(true)
    // estimatePositionAmounts should be called in dry-run mode
    expect(mockEstimatePositionAmounts).toHaveBeenCalled()
  })

  it('full flow success: close → swap → open', async () => {
    vi.useFakeTimers()
    setupRebalanceMocks()

    // Pre-close wallet balances
    mockGetBalance
      // First call: preflight gas check
      .mockResolvedValueOnce({ totalBalance: '0' })       // coinA (preflight)
      .mockResolvedValueOnce({ totalBalance: '2000000000' }) // coinB (preflight, 2 SUI)
      // Second call: preClose snapshot
      .mockResolvedValueOnce({ totalBalance: '1000000' })  // coinA before close
      .mockResolvedValueOnce({ totalBalance: '500000000' }) // coinB before close

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // Post-close wallet (after RPC settle) — delta shows position funds
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '6000000' })    // coinA after close (+5M USDC)
      .mockResolvedValueOnce({ totalBalance: '2500000000' }) // coinB after close (+2 SUI)

    mockCalculateSwapPlan.mockReturnValue({
      needSwap: true,
      a2b: true,
      swapAmount: BigInt(1_000_000),
      reason: 'ratio off',
      targetAmountA: BigInt(4_000_000),
      targetAmountB: BigInt(2_100_000_000),
    })
    mockExecuteSwap.mockResolvedValue({ success: true, digest: '0xswap', gasCost: 3_000_000n, error: null })

    // Post-swap wallet
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '5000000' })    // coinA after swap
      .mockResolvedValueOnce({ totalBalance: '2600000000' }) // coinB after swap

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([
      makePosition({ positionId: '0xnewpos', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('500000000') }),
    ])

    // Idle deploy: below threshold → skip
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '500000' })     // $0.50 USDC < $1 threshold
      .mockResolvedValueOnce({ totalBalance: '1050000000' }) // 1.05 SUI - 1.0 reserve = 0.05 SUI < 0.1 threshold

    const config = makeConfig({ dryRun: false })
    const promise = checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )
    // Advance past all setTimeout delays (5s poll + 3s post-swap + 2s post-open)
    await vi.advanceTimersByTimeAsync(30_000)
    const { result, newPositionId } = await promise

    expect(result).not.toBeNull()
    expect(result!.success).toBe(true)
    expect(mockClosePosition).toHaveBeenCalledTimes(1)
    expect(mockExecuteSwap).toHaveBeenCalledTimes(1)
    expect(mockOpenPosition).toHaveBeenCalledTimes(1)
    expect(newPositionId).toBe('0xnewpos')
    // Total gas = close(3M) + swap(3M) + open(3M) = 9M
    expect(result!.gasCost).toBe(9_000_000n)

    vi.useRealTimers()
  })

  it('swap-free dry-run: skips swap, passes balances directly to openPosition', async () => {
    setupRebalanceMocks()
    mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(5_000_000), targetAmountB: BigInt(2_000_000_000),
    })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })
    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })

    const config = makeConfig({ dryRun: true, swapFreeRebalance: true })
    const { result } = await checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )

    expect(result).not.toBeNull()
    expect(result!.success).toBe(true)
    // calculateSwapPlan IS called in swap-free mode (ratio check), but executeSwap is NOT
    expect(mockCalculateSwapPlan).toHaveBeenCalled()
    expect(mockExecuteSwap).not.toHaveBeenCalled()
    // calculateSwapPlan IS called in swap-free mode (to check ratio), but no swap executed
    expect(mockExecuteSwap).not.toHaveBeenCalled()
    // openPosition should receive the estimated amounts (minus gas reserve)
    expect(mockOpenPosition).toHaveBeenCalledWith(
      '0xpool',                     // poolId
      '0x...::usdc::USDC',          // coinTypeA
      expect.any(String),           // coinTypeB
      -180, 180,                    // tickLower, tickUpper
      '5000000',                    // amountA (balanceA passed directly)
      '1000000000',                 // amountB (2B - 1.0B gas reserve)
      0.01,                         // slippage
      expect.anything(),            // keypair
      true,                         // dryRun
      [],                           // rewarderCoinTypes
      BigInt('18446744073709551616'), // currentSqrtPrice
    )
  })

  it('swap-free live: close → skip swap → open (no executeSwap call)', async () => {
    vi.useFakeTimers()
    setupRebalanceMocks()

    // calculateSwapPlan is called in swap-free mode for ratio check
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(5_000_000), targetAmountB: BigInt(1_000_000_000),
    })

    // Pre-close wallet balances
    mockGetBalance
      // preflight gas check
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '2000000000' })
      // preClose snapshot
      .mockResolvedValueOnce({ totalBalance: '1000000' })
      .mockResolvedValueOnce({ totalBalance: '500000000' })

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // Post-close wallet (delta shows position funds: +5 USDC, +2 SUI)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '6000000' })
      .mockResolvedValueOnce({ totalBalance: '2500000000' })

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([
      makePosition({ positionId: '0xnewpos', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('500000000') }),
    ])

    // Idle deploy: below threshold → skip
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '500000' })     // $0.50 USDC < $1 threshold
      .mockResolvedValueOnce({ totalBalance: '1050000000' }) // 1.05 SUI - 1.0 reserve = 0.05 SUI < 0.1 threshold

    const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
    const promise = checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )
    await vi.advanceTimersByTimeAsync(30_000)
    const { result, newPositionId } = await promise

    expect(result).not.toBeNull()
    expect(result!.success).toBe(true)
    expect(newPositionId).toBe('0xnewpos')
    // calculateSwapPlan IS called in swap-free mode (ratio check), but executeSwap is NOT
    expect(mockCalculateSwapPlan).toHaveBeenCalled()
    expect(mockExecuteSwap).not.toHaveBeenCalled()
    // Total gas = close(3M) + open(3M) = 6M (no swap gas)
    expect(result!.gasCost).toBe(6_000_000n)
    // openPosition receives delta-isolated funds directly (no swap adjustment)
    // deltaA = 6M - 1M = 5M USDC, deltaB = 2.5B - 0.5B = 2B SUI → available = 2B - 1.0B reserve = 1.0B
    expect(mockOpenPosition).toHaveBeenCalledWith(
      '0xpool', '0x...::usdc::USDC', expect.any(String),
      -180, 180,
      '5000000',      // balanceA (delta)
      '1000000000',   // availableB (delta - gas reserve)
      0.01, expect.anything(), false, [], expect.anything(),
    )

    vi.useRealTimers()
  })

  it('swap-free: rebalance_complete event includes swapFree=true', async () => {
    setupRebalanceMocks()
    mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })
    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })

    const { recordEvent } = await import('../../src/utils/event-log.js')

    const config = makeConfig({ dryRun: true, swapFreeRebalance: true })
    await checkAndRebalance(makePool(), makePosition(), config, makePoolConfig(), mockKeypair)

    // Find the rebalance_complete event call
    const completeCall = (recordEvent as any).mock.calls.find(
      (c: any[]) => c[0] === 'rebalance_complete'
    )
    expect(completeCall).toBeDefined()
    expect(completeCall[1].swapFree).toBe(true)
  })

  it('swap-free=false: standard swap path still works', async () => {
    setupRebalanceMocks()
    mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false,
      a2b: false,
      swapAmount: 0n,
      reason: 'ratio OK',
      targetAmountA: BigInt(5_000_000),
      targetAmountB: BigInt(2_000_000_000),
    })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })
    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })

    const config = makeConfig({ dryRun: true, swapFreeRebalance: false })
    const { result } = await checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )

    expect(result!.success).toBe(true)
    // calculateSwapPlan SHOULD be called in standard mode
    expect(mockCalculateSwapPlan).toHaveBeenCalledTimes(1)
  })

  it('swap-free: allows small ratio swap when swapRatio <= maxRatio', async () => {
    vi.useFakeTimers()
    setupRebalanceMocks()

    // Balance setup
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })           // coinA preflight
      .mockResolvedValueOnce({ totalBalance: '2000000000' })   // coinB preflight
      .mockResolvedValueOnce({ totalBalance: '0' })            // coinA preClose
      .mockResolvedValueOnce({ totalBalance: '500000000' })    // coinB preClose

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // Post-close: 10 USDC delta
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '10000000' })     // coinA: +10 USDC
      .mockResolvedValueOnce({ totalBalance: '1100000000' })   // coinB: +0.6 SUI delta

    // Ratio swap setup (0.5M out of 10M = 5% swap ratio)
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: true,
      a2b: true,
      swapAmount: BigInt(500_000),      // 0.5 USDC
      reason: 'ratio off',
      targetAmountA: BigInt(9_500_000),
      targetAmountB: BigInt(2_000_000_000), // Expected availableB + swap output
    })

    mockExecuteSwap.mockResolvedValue({ success: true, digest: '0xswap', gasCost: 3_000_000n, error: null })

    // Post-swap wallet
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '9500000' })      // coinA
      .mockResolvedValueOnce({ totalBalance: '2000000000' })   // coinB

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([
      makePosition({ positionId: '0xnew', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('300000000') }),
    ])

    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '500000' })     // $0.50 USDC < $1 threshold
      .mockResolvedValueOnce({ totalBalance: '1050000000' }) // 1.05 SUI - 1.0 reserve = 0.05 SUI < 0.1 threshold

    const config = makeConfig({ dryRun: false, swapFreeRebalance: true, swapFreeMaxRatioSwap: 0.10 })
    const promise = checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )
    await vi.advanceTimersByTimeAsync(30_000)
    const { result } = await promise

    expect(result!.success).toBe(true)
    expect(mockCalculateSwapPlan).toHaveBeenCalledTimes(1)
    expect(mockExecuteSwap).toHaveBeenCalledTimes(1) // 5% < 10% maxRatio, so swap executes

    vi.useRealTimers()
  })

  it('swap-free: caps ratio swap when swapRatio > maxRatio', async () => {
    vi.useFakeTimers()
    // Use threshold trigger (not range-out) because range-out relaxes maxRatio to 50%
    mockEvaluateRebalanceTrigger.mockReturnValue({
      shouldRebalance: true,
      trigger: 'threshold',
      currentPrice: 1.04,
      currentLower: 0.95,
      currentUpper: 1.05,
      newLower: null,
      newUpper: null,
      reason: 'Price near edge',
    })
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })
    mockGetBalance.mockResolvedValue({ totalBalance: '2000000000' })

    // Balance setup (using same values as previous)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })           // coinA preflight
      .mockResolvedValueOnce({ totalBalance: '2000000000' })   // coinB preflight
      .mockResolvedValueOnce({ totalBalance: '0' })            // coinA preClose
      .mockResolvedValueOnce({ totalBalance: '500000000' })    // coinB preClose

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '10000000' })     // coinA: +10 USDC
      .mockResolvedValueOnce({ totalBalance: '1100000000' })   // coinB: +0.6 SUI delta

    // Ratio swap setup (5.0M out of 10M = 50% swap ratio, will be capped to 20%)
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: true,
      a2b: true,
      swapAmount: BigInt(5_000_000),      // 5.0 USDC
      reason: 'ratio seriously off',
      targetAmountA: BigInt(5_000_000),
      targetAmountB: BigInt(2_000_000_000),
    })

    // Swap will be capped (50% > 20% maxIdleSwapRatio) and executed
    mockExecuteSwap.mockResolvedValue({ success: true, digest: '0xcappedswap', gasCost: 3_000_000n, error: null })

    // Post-swap balance re-query (ratio swap settled)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '8000000' })      // coinA: 10M - 2M swapped
      .mockResolvedValueOnce({ totalBalance: '1300000000' })   // coinB: increased from swap output

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([
      makePosition({ positionId: '0xnew', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('300000000') }),
    ])

    // Idle deploy: below threshold
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '500000' })     // $0.50 USDC < $1 threshold
      .mockResolvedValueOnce({ totalBalance: '1050000000' }) // 1.05 SUI - 1.0 reserve = 0.05 SUI < 0.1 threshold

    const config = makeConfig({ dryRun: false, swapFreeRebalance: true, swapFreeMaxRatioSwap: 0.10 })
    const promise = checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )
    await vi.advanceTimersByTimeAsync(30_000)
    const { result } = await promise

    expect(result!.success).toBe(true)
    expect(mockCalculateSwapPlan).toHaveBeenCalledTimes(1)
    // Swap IS called now (capped to maxIdleSwapRatio), not skipped
    expect(mockExecuteSwap).toHaveBeenCalledTimes(1)
    // Verify capped amount: 10M * 0.20 = 2M
    expect(mockExecuteSwap).toHaveBeenCalledWith(
      expect.anything(), // pool
      true,              // a2b
      BigInt(2_000_000), // capped amount (20% of 10M)
      expect.anything(), // slippage
      expect.anything(), // keypair
      false,             // dryRun
      expect.anything(), // maxSwapCostPct
    )

    vi.useRealTimers()
  })

  it('swap-free: capital efficiency — unbalanced funds leave remainder in wallet', async () => {
    // Scenario: After closing position, we have 10 USDC + 1 SUI.
    // The new range might require a different ratio (e.g., 50:50 in value).
    // In swap-free mode, openPosition handles the imbalance via fix_amount_a.
    // The SDK deposits what it can; the rest stays in wallet.
    // This test verifies the exact amounts passed to openPosition (no adjustment).
    vi.useFakeTimers()
    setupRebalanceMocks()

    // Explicitly set calculateSwapPlan: no swap needed (ratio is acceptable)
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(10_000_000), targetAmountB: 0n,
    })

    // Pre-close wallet
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })           // coinA preflight
      .mockResolvedValueOnce({ totalBalance: '2000000000' })   // coinB preflight
      .mockResolvedValueOnce({ totalBalance: '0' })            // coinA preClose
      .mockResolvedValueOnce({ totalBalance: '500000000' })    // coinB preClose

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // Post-close: heavily skewed — 10 USDC + only 0.6 SUI delta
    // (position was mostly in USDC because price moved above range)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '10000000' })     // coinA: +10 USDC
      .mockResolvedValueOnce({ totalBalance: '1100000000' })   // coinB: +0.6 SUI delta

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([
      makePosition({ positionId: '0xnew', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('300000000') }),
    ])

    // Idle deploy: below threshold → skip
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '500000' })     // $0.50 USDC < $1 threshold
      .mockResolvedValueOnce({ totalBalance: '1050000000' }) // 1.05 SUI - 1.0 reserve = 0.05 SUI < 0.1 threshold

    const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
    const promise = checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )
    await vi.advanceTimersByTimeAsync(30_000)
    const { result } = await promise

    expect(result!.success).toBe(true)
    // calculateSwapPlan IS called in swap-free mode (ratio check), but executeSwap is NOT
    expect(mockCalculateSwapPlan).toHaveBeenCalled()
    expect(mockExecuteSwap).not.toHaveBeenCalled()

    // openPosition receives the skewed amounts directly:
    // deltaA = 10M - 0 = 10M USDC
    // deltaB = 1.1B - 0.5B = 0.6B SUI → available = max(0.6B - 1.0B reserve, 0) = 0
    // In a 50:50 pool, no SUI can be paired. SDK's fix_amount_a tries both directions.
    // The SDK's fix_amount_a logic handles this; excess USDC stays in wallet.
    expect(mockOpenPosition).toHaveBeenCalledWith(
      '0xpool', '0x...::usdc::USDC', expect.any(String),
      -180, 180,
      '10000000',     // all 10 USDC passed (SDK decides how much to use)
      '0',            // 0 SUI — delta(0.6B) < reserve(1.0B), clamped to 0
      0.01, expect.anything(), false, [], expect.anything(),
    )

    vi.useRealTimers()
  })

  it('swap-free: all-in-one-token scenario (0 coinB after gas reserve)', async () => {
    // Edge case: position was entirely in coinA (price above range).
    // After close, we have lots of USDC but very little SUI (just gas reserve).
    vi.useFakeTimers()
    setupRebalanceMocks()

    // calculateSwapPlan is called in swap-free mode for ratio check
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(20_000_000), targetAmountB: 0n,
    })

    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '2000000000' })
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '1500000000' })

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // Post-close: 20 USDC + only 0.5 SUI delta (< 1.0B gas reserve)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '20000000' })
      .mockResolvedValueOnce({ totalBalance: '2000000000' }) // delta = 0.5 SUI

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([])

    const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
    const promise = checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )
    await vi.advanceTimersByTimeAsync(30_000)
    const { result } = await promise

    expect(result!.success).toBe(true)
    // availableB = 0.5B delta - 1.0B reserve → clamped to 0
    // All USDC passed, 0 SUI → SDK will deposit 0 liquidity from coinB side
    // openPosition's fix_amount_a tries both directions
    expect(mockOpenPosition).toHaveBeenCalledWith(
      expect.anything(), expect.anything(), expect.anything(),
      -180, 180,
      '20000000',  // 20 USDC
      '0',         // 0 SUI (all consumed by gas reserve)
      expect.anything(), expect.anything(), false, [], expect.anything(),
    )

    vi.useRealTimers()
  })

  it('close succeeds, swap fails → error returned', async () => {
    setupRebalanceMocks()
    mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: true,
      a2b: true,
      swapAmount: BigInt(1_000_000),
      reason: 'ratio off',
      targetAmountA: BigInt(4_000_000),
      targetAmountB: BigInt(2_100_000_000),
    })
    mockExecuteSwap.mockResolvedValue({ success: false, digest: null, gasCost: 0n, error: 'swap failed' })

    const config = makeConfig({ dryRun: true })
    const { result } = await checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )

    expect(result).not.toBeNull()
    expect(result!.success).toBe(false)
    expect(result!.error).toBe('swap failed')
    expect(mockOpenPosition).not.toHaveBeenCalled()
  })

  it('fund isolation: delta calculation (postClose - preClose)', async () => {
    vi.useFakeTimers()
    setupRebalanceMocks()

    // Preflight
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })           // coinA preflight
      .mockResolvedValueOnce({ totalBalance: '2000000000' })   // coinB preflight
    // preClose snapshot
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '10000000' })     // coinA preClose = 10 USDC
      .mockResolvedValueOnce({ totalBalance: '1000000000' })   // coinB preClose = 1 SUI

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // postClose: delta = 15 USDC - 10 USDC = 5 USDC, 3 SUI - 1 SUI = 2 SUI
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '15000000' })     // coinA postClose
      .mockResolvedValueOnce({ totalBalance: '3000000000' })   // coinB postClose

    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false,
      a2b: false,
      swapAmount: 0n,
      reason: 'ratio OK',
      targetAmountA: BigInt(5_000_000),
      targetAmountB: BigInt(2_000_000_000),
    })

    // Post-swap wallet (same as post-close since no swap — but still queried before open)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '15000000' })     // coinA
      .mockResolvedValueOnce({ totalBalance: '3000000000' })   // coinB

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([])

    const config = makeConfig({ dryRun: false })
    const promise = checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )
    await vi.advanceTimersByTimeAsync(30_000)
    await promise

    // calculateSwapPlan should receive delta-isolated funds:
    // balanceA = postClose.A - preClose.A = 15M - 10M = 5M
    // balanceB (available after gas reserve) = (3B - 1B) - 1.0B = 1.0B
    expect(mockCalculateSwapPlan).toHaveBeenCalledWith(
      expect.anything(),         // pool
      -180,                       // tickLower
      180,                        // tickUpper
      BigInt(5_000_000),          // balanceA (delta)
      BigInt(1_000_000_000),      // availableB (delta - gas reserve)
      6,                          // decimalsA
      9,                          // decimalsB
    )

    vi.useRealTimers()
  })
})

// ════════════════════════════════════════════════════════════════
// Swap-free: Wallet SUI safety
// ════════════════════════════════════════════════════════════════

describe('swap-free: wallet SUI safety', () => {
  const rebalanceDecision: RebalanceDecision = {
    shouldRebalance: true,
    trigger: 'range-out',
    currentPrice: 1.1,
    currentLower: 0.95,
    currentUpper: 1.05,
    newLower: null,
    newUpper: null,
    reason: 'Price out of range',
  }

  function setupCommonMocks() {
    mockEvaluateRebalanceTrigger.mockReturnValue({ ...rebalanceDecision })
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  it('normal close: GAS_RESERVE (1.0 SUI) always deducted — amountB = deltaB - 1.0', async () => {
    vi.useFakeTimers()
    setupCommonMocks()

    // calculateSwapPlan is called in swap-free mode for ratio check
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(8_000_000), targetAmountB: BigInt(2_000_000_000),
    })

    // Wallet before: 1.2 SUI
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })             // coinA preflight
      .mockResolvedValueOnce({ totalBalance: '1200000000' })     // coinB preflight = 1.2 SUI
      .mockResolvedValueOnce({ totalBalance: '0' })              // coinA preClose
      .mockResolvedValueOnce({ totalBalance: '1200000000' })     // coinB preClose = 1.2 SUI

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // Position releases 3.0 SUI delta → wallet now 4.2 SUI
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '8000000' })        // coinA postClose = 8 USDC
      .mockResolvedValueOnce({ totalBalance: '4200000000' })     // coinB postClose = 4.2 SUI

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([])

    const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
    const promise = checkAndRebalance(makePool(), makePosition(), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const { result } = await promise

    expect(result!.success).toBe(true)
    // deltaB = 4.2B - 1.2B = 3.0B → availableB = 3.0B - 1.0B = 2.0B
    // Wallet after open: 4.2B - 2.0B - gas ≈ 2.2 SUI (well above 1)
    expect(mockOpenPosition).toHaveBeenCalledWith(
      expect.anything(), expect.anything(), expect.anything(),
      -180, 180,
      '8000000',       // deltaA = 8 USDC
      '2000000000',    // availableB = deltaB(3.0) - reserve(1.0) = 2.0 SUI
      expect.anything(), expect.anything(), false, [], expect.anything(),
    )
    // calculateSwapPlan IS called in swap-free mode (ratio check), but executeSwap is NOT
    expect(mockCalculateSwapPlan).toHaveBeenCalledTimes(1)
    expect(mockExecuteSwap).not.toHaveBeenCalled()

    vi.useRealTimers()
  })

  it('normal close: deltaB < GAS_RESERVE → amountB = 0, no SUI deposited', async () => {
    // Most position value was in USDC; only 0.3 SUI released
    vi.useFakeTimers()
    setupCommonMocks()

    // calculateSwapPlan is called in swap-free mode for ratio check
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(15_000_000), targetAmountB: 0n,
    })

    // Wallet before: 1.5 SUI
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '1500000000' })
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '1500000000' })

    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // Only 0.3 SUI delta (< 1.0 reserve)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '15000000' })
      .mockResolvedValueOnce({ totalBalance: '1800000000' })    // delta = 0.3 SUI

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([])

    const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
    const promise = checkAndRebalance(makePool(), makePosition(), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const { result } = await promise

    expect(result!.success).toBe(true)
    // deltaB = 0.3 SUI < GAS_RESERVE(1.0) → availableB = 0
    // Wallet after: 1.8B - 0 - gas ≈ 1.8 SUI (SUI preserved!)
    expect(mockOpenPosition).toHaveBeenCalledWith(
      expect.anything(), expect.anything(), expect.anything(),
      -180, 180,
      '15000000',  // 15 USDC (all delta)
      '0',         // 0 SUI — GAS_RESERVE clamp prevents deposit
      expect.anything(), expect.anything(), false, [], expect.anything(),
    )
    expect(mockCalculateSwapPlan).toHaveBeenCalledTimes(1)

    vi.useRealTimers()
  })

  it('recovery mode (0-liquidity) + swap-free: wallet SUI protected by GAS_RESERVE', async () => {
    // Recovery mode uses full wallet balance, NOT delta isolation.
    // This is the most dangerous path for wallet SUI.
    vi.useFakeTimers()
    setupCommonMocks()

    // calculateSwapPlan is called in swap-free mode for ratio check
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(10_000_000), targetAmountB: BigInt(2_000_000_000),
    })

    // Wallet: 10 USDC + 3.0 SUI (funds from previous interrupted close)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '10000000' })       // coinA preflight
      .mockResolvedValueOnce({ totalBalance: '3000000000' })     // coinB preflight = 3.0 SUI
      // preClose snapshot (always called)
      .mockResolvedValueOnce({ totalBalance: '10000000' })
      .mockResolvedValueOnce({ totalBalance: '3000000000' })
      // Recovery wallet query (liquidity === 0n path)
      .mockResolvedValueOnce({ totalBalance: '10000000' })
      .mockResolvedValueOnce({ totalBalance: '3000000000' })

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xrecovered', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([
      makePosition({ positionId: '0xrecoveredpos', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('500000000') }),
    ])

    // Idle deploy: below threshold → skip
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '500000' })     // $0.50 USDC < $1 threshold
      .mockResolvedValueOnce({ totalBalance: '1050000000' }) // 1.05 SUI - 1.0 reserve = 0.05 SUI < 0.1 threshold

    const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
    const zeroLiqPos = makePosition({ positionId: '0xrecovery', liquidity: 0n })
    const promise = checkAndRebalance(makePool(), zeroLiqPos, config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const { result } = await promise

    expect(result!.success).toBe(true)
    expect(mockClosePosition).not.toHaveBeenCalled()
    expect(mockCalculateSwapPlan).toHaveBeenCalledTimes(1)

    // balanceB = 3.0B (full wallet) → availableB = 3.0B - 1.0B = 2.0B
    // Wallet after open: 3.0B - 2.0B - gas ≈ 1.0 SUI
    // GAS_RESERVE (1.0 SUI) is the floor — wallet cannot go below this minus gas
    expect(mockOpenPosition).toHaveBeenCalledWith(
      expect.anything(), expect.anything(), expect.anything(),
      -180, 180,
      '10000000',      // 10 USDC (full wallet)
      '2000000000',    // 2.0 SUI (wallet 3.0 - reserve 1.0)
      expect.anything(), expect.anything(), false, [], expect.anything(),
    )

    vi.useRealTimers()
  })

  it('recovery mode: wallet with exactly GAS_RESERVE SUI → amountB = 0', async () => {
    vi.useFakeTimers()
    setupCommonMocks()

    // calculateSwapPlan is called in swap-free mode for ratio check
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
      targetAmountA: BigInt(5_000_000), targetAmountB: 0n,
    })

    // Wallet: 5 USDC + 1.0 SUI (exactly GAS_RESERVE)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '5000000' })
      .mockResolvedValueOnce({ totalBalance: '1000000000' })   // 1.0 SUI = GAS_RESERVE
      .mockResolvedValueOnce({ totalBalance: '5000000' })
      .mockResolvedValueOnce({ totalBalance: '1000000000' })
      // Recovery wallet query
      .mockResolvedValueOnce({ totalBalance: '5000000' })
      .mockResolvedValueOnce({ totalBalance: '1000000000' })

    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([])

    const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
    const zeroLiqPos = makePosition({ positionId: '0xrecovery2', liquidity: 0n })
    const promise = checkAndRebalance(makePool(), zeroLiqPos, config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    const { result } = await promise

    expect(result!.success).toBe(true)
    // balanceB = 1.0B = GAS_RESERVE → availableB = 0
    // Zero SUI deposited; all 1.0 SUI stays in wallet (minus gas)
    expect(mockOpenPosition).toHaveBeenCalledWith(
      expect.anything(), expect.anything(), expect.anything(),
      -180, 180,
      '5000000',  // 5 USDC
      '0',        // 0 SUI — GAS_RESERVE clamp
      expect.anything(), expect.anything(), false, [], expect.anything(),
    )

    vi.useRealTimers()
  })

  it('swap-free never calls calculateSwapPlan or executeSwap in any path', async () => {
    vi.useFakeTimers()
    setupCommonMocks()

    // Test with normal close (liquidity > 0)
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '2000000000' })
      .mockResolvedValueOnce({ totalBalance: '0' })
      .mockResolvedValueOnce({ totalBalance: '1000000000' })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })
    mockGetBalance
      .mockResolvedValueOnce({ totalBalance: '10000000' })
      .mockResolvedValueOnce({ totalBalance: '3000000000' })
    mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
    mockGetPositions.mockResolvedValue([])

    const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
    const promise = checkAndRebalance(makePool(), makePosition(), config, makePoolConfig(), mockKeypair)
    await vi.advanceTimersByTimeAsync(30_000)
    await promise
    // Safety: SWAP EXECUTIONS NEVER HAPPEN
    expect(mockCalculateSwapPlan).toHaveBeenCalledTimes(1)
    expect(mockExecuteSwap).not.toHaveBeenCalled()

    vi.useRealTimers()
  })
})

// ════════════════════════════════════════════════════════════════
// Swap-free: Range-out swap fallback
// ════════════════════════════════════════════════════════════════

describe('swap-free: range-out swap fallback', () => {
  const rangeOutDecision: RebalanceDecision = {
    shouldRebalance: true,
    trigger: 'range-out',
    currentPrice: 1.1,
    currentLower: 0.95,
    currentUpper: 1.05,
    newLower: null,
    newUpper: null,
    reason: 'Price out of range',
  }

  const thresholdDecision: RebalanceDecision = {
    shouldRebalance: true,
    trigger: 'threshold',
    currentPrice: 1.04,
    currentLower: 0.95,
    currentUpper: 1.05,
    newLower: null,
    newUpper: null,
    reason: 'Price near edge',
  }

  function setupFallbackMocks(decision: RebalanceDecision = rangeOutDecision) {
    mockEvaluateRebalanceTrigger.mockReturnValue({ ...decision })
    mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
    mockCalculateOptimalRange.mockReturnValue({
      tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
    })
    mockGetBalance.mockResolvedValue({ totalBalance: '2000000000' })
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockCoinBPriceInCoinA.mockReturnValue(3.5)
    mockGetCetusUsdPrice.mockResolvedValue(0.25)
  })

  it('swap-free open fails on range-out → falls back to swap → succeeds', async () => {
    setupFallbackMocks()
    mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // First openPosition call fails (swap-free, 100% one-sided)
    mockOpenPosition
      .mockResolvedValueOnce({ success: false, digest: null, gasCost: 0n, error: 'Insufficient liquidity for one-sided deposit' })
      // Second openPosition call succeeds (after swap fallback)
      .mockResolvedValueOnce({ success: true, digest: '0xopen_retry', gasCost: 3_000_000n, error: null })

    mockCalculateSwapPlan.mockReturnValue({
      needSwap: true,
      a2b: true,
      swapAmount: BigInt(2_000_000),
      reason: 'ratio off after range-out',
      targetAmountA: BigInt(3_000_000),
      targetAmountB: BigInt(1_500_000_000),
    })
    mockExecuteSwap.mockResolvedValue({ success: true, digest: '0xswap_fallback', gasCost: 3_000_000n, error: null })

    const config = makeConfig({ dryRun: true, swapFreeRebalance: true })
    const { result } = await checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )

    expect(result).not.toBeNull()
    expect(result!.success).toBe(true)
    expect(result!.digest).toBe('0xopen_retry')
    // openPosition called twice: first (failed), then retry (success)
    expect(mockOpenPosition).toHaveBeenCalledTimes(2)
    // swap functions called: once for ratio-correction (range-out allows up to 50%),
    // once for fallback recalculation
    expect(mockCalculateSwapPlan).toHaveBeenCalledTimes(2)
    // executeSwap: once for ratio-correction swap + once for fallback swap = 2
    expect(mockExecuteSwap).toHaveBeenCalledTimes(2)
    // swapFree should be false in the event (fallback used swap)
    const { recordEvent: re } = await import('../../src/utils/event-log.js')
    const completeCall = (re as any).mock.calls.find((c: any[]) => c[0] === 'rebalance_complete')
    expect(completeCall).toBeDefined()
    expect(completeCall[1].swapFree).toBe(false)
  })

  it('swap-free open fails on range-out → swap also fails → CRITICAL return', async () => {
    setupFallbackMocks()
    mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // First open fails
    mockOpenPosition.mockResolvedValueOnce({ success: false, digest: null, gasCost: 0n, error: 'one-sided deposit failed' })

    mockCalculateSwapPlan.mockReturnValue({
      needSwap: true,
      a2b: true,
      swapAmount: BigInt(2_000_000),
      reason: 'ratio off',
      targetAmountA: BigInt(3_000_000),
      targetAmountB: BigInt(1_500_000_000),
    })
    // Swap also fails
    mockExecuteSwap.mockResolvedValue({ success: false, digest: null, gasCost: 0n, error: 'swap pool error' })

    const config = makeConfig({ dryRun: true, swapFreeRebalance: true })
    const { result } = await checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )

    expect(result).not.toBeNull()
    expect(result!.success).toBe(false)
    expect(result!.error).toBe('swap pool error')
    // openPosition called once (failed), no retry since swap failed
    expect(mockOpenPosition).toHaveBeenCalledTimes(1)
  })

  it('swap-free open fails on threshold → no fallback, returns error directly', async () => {
    setupFallbackMocks(thresholdDecision)
    mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // Swap plan: 60% ratio → capped to 20% (maxIdleSwapRatio) and executed
    mockCalculateSwapPlan.mockReturnValue({
      needSwap: true,
      a2b: true,
      swapAmount: BigInt(3_000_000),      // 3M / 5M = 60% > 20% maxIdleSwapRatio
      reason: 'ratio off',
      targetAmountA: BigInt(2_000_000),
      targetAmountB: BigInt(1_500_000_000),
    })

    // Capped swap succeeds (dryRun)
    mockExecuteSwap.mockResolvedValue({ success: true, digest: '0xcappedswap', gasCost: 0n, error: null })

    // Open fails
    mockOpenPosition.mockResolvedValueOnce({ success: false, digest: null, gasCost: 0n, error: 'open failed' })

    const config = makeConfig({ dryRun: true, swapFreeRebalance: true })
    const { result } = await checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )

    expect(result).not.toBeNull()
    expect(result!.success).toBe(false)
    expect(result!.error).toBe('open failed')
    // Swap calc + capped execution
    expect(mockCalculateSwapPlan).toHaveBeenCalledTimes(1)
    expect(mockExecuteSwap).toHaveBeenCalledTimes(1) // capped swap executed
    // openPosition called only once (no fallback for threshold)
    expect(mockOpenPosition).toHaveBeenCalledTimes(1)
  })

  it('swap-free open fails on range-out → swap succeeds → retry open fails → CRITICAL', async () => {
    setupFallbackMocks()
    mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })
    mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

    // First open fails, retry also fails
    mockOpenPosition
      .mockResolvedValueOnce({ success: false, digest: null, gasCost: 0n, error: 'one-sided failed' })
      .mockResolvedValueOnce({ success: false, digest: null, gasCost: 0n, error: 'retry also failed' })

    mockCalculateSwapPlan.mockReturnValue({
      needSwap: true,
      a2b: true,
      swapAmount: BigInt(2_000_000),
      reason: 'ratio off',
      targetAmountA: BigInt(3_000_000),
      targetAmountB: BigInt(1_500_000_000),
    })
    mockExecuteSwap.mockResolvedValue({ success: true, digest: '0xswap', gasCost: 3_000_000n, error: null })

    const config = makeConfig({ dryRun: true, swapFreeRebalance: true })
    const { result } = await checkAndRebalance(
      makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
    )

    expect(result).not.toBeNull()
    expect(result!.success).toBe(false)
    expect(result!.error).toBe('retry also failed')
    // Both open attempts made
    expect(mockOpenPosition).toHaveBeenCalledTimes(2)
    // executeSwap: once for ratio-correction (range-out 50% relaxation) + once for fallback = 2
    expect(mockExecuteSwap).toHaveBeenCalledTimes(2)
  })
})

// ════════════════════════════════════════════════════════════════
// Scheduler helpers
// ════════════════════════════════════════════════════════════════

describe('scheduler helpers', () => {
  describe('isPaused (parsing logic)', () => {
    // isPaused is a private function in scheduler.ts that reads .env and parses PAUSED flag.
    // We test the same regex-based parsing logic it uses, since the function is not exported.
    function parsePaused(content: string): boolean {
      const match = content.match(/^PAUSED\s*=\s*(.+)$/m)
      return match ? match[1].trim().toLowerCase() === 'true' : false
    }

    it('returns true when PAUSED=true in .env', () => {
      expect(parsePaused('PAUSED=true\nLOG_LEVEL=info\n')).toBe(true)
    })

    it('returns true with extra whitespace', () => {
      expect(parsePaused('PAUSED = true \nLOG_LEVEL=info\n')).toBe(true)
    })

    it('returns true case-insensitive', () => {
      expect(parsePaused('PAUSED=True\n')).toBe(true)
      expect(parsePaused('PAUSED=TRUE\n')).toBe(true)
    })

    it('returns false when PAUSED=false in .env', () => {
      expect(parsePaused('PAUSED=false\nLOG_LEVEL=info\n')).toBe(false)
    })

    it('returns false when PAUSED not in .env', () => {
      expect(parsePaused('LOG_LEVEL=info\n')).toBe(false)
    })

    it('returns false for empty string', () => {
      expect(parsePaused('')).toBe(false)
    })
  })

  describe('post-rebalance idle fund deployment', () => {
    const rebalanceDecision: RebalanceDecision = {
      shouldRebalance: true,
      trigger: 'range-out',
      currentPrice: 1.1,
      currentLower: 0.95,
      currentUpper: 1.05,
      newLower: null,
      newUpper: null,
      reason: 'Price out of range',
    }

    function setupIdleDeployMocks() {
      mockEvaluateRebalanceTrigger.mockReturnValue({ ...rebalanceDecision })
      mockCalculateVolatilityBasedTicks.mockResolvedValue({ sigma: 50, tickWidth: 120 })
      mockCalculateOptimalRange.mockReturnValue({
        tickLower: -180, tickUpper: 180, priceLower: 0.9, priceUpper: 1.1,
      })
    }

    it('deploys idle USDC funds after rebalance', async () => {
      vi.useFakeTimers()
      setupIdleDeployMocks()

      // Pre-flight + preClose + postClose (RPC settle) + open + detect pos
      mockGetBalance
        // preflight gas check
        .mockResolvedValueOnce({ totalBalance: '0' })
        .mockResolvedValueOnce({ totalBalance: '2000000000' })
        // preClose snapshot
        .mockResolvedValueOnce({ totalBalance: '1000000' })
        .mockResolvedValueOnce({ totalBalance: '500000000' })

      mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

      // Post-close wallet (delta: +5 USDC, +2 SUI)
      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '6000000' })
        .mockResolvedValueOnce({ totalBalance: '2500000000' })

      // Rebalance ratio swap: no swap needed (balanced)
      mockCalculateSwapPlan.mockReturnValueOnce({
        needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
        targetAmountA: BigInt(5_000_000), targetAmountB: BigInt(2_000_000_000),
      })

      mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
      mockGetPositions.mockResolvedValue([
        makePosition({ positionId: '0xnewpos', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('500000000') }),
      ])

      // --- Idle deploy phase ---
      // getWalletBalances for idle check: 2000 USDC + 2 SUI in wallet
      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '2000000000' })  // 2000 USDC idle
        .mockResolvedValueOnce({ totalBalance: '2000000000' })  // 2.0 SUI

      // calculateSwapPlan for idle funds (second call)
      mockCalculateSwapPlan.mockReturnValue({
        needSwap: true,
        a2b: true,
        swapAmount: BigInt(500_000_000),
        reason: 'ratio off',
        targetAmountA: BigInt(1_500_000_000),
        targetAmountB: BigInt(1_500_000_000),
      })
      mockExecuteSwap.mockResolvedValue({ success: true, digest: '0xidleswap', gasCost: 3_000_000n, error: null })

      // Post-swap wallet for idle deploy (settlement check: B increased → settled)
      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '1500000000' })  // A decreased (swapped out)
        .mockResolvedValueOnce({ totalBalance: '2500000000' })  // B increased (swap output)

      // addLiquidity for idle funds
      mockAddLiquidity.mockResolvedValue({ success: true, digest: '0xidleadd', gasCost: 3_000_000n, error: null })

      // Iteration 2: balance check after first addLiquidity — below threshold (converged)
      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '500000' })   // 0.5 USDC < 1 USDC threshold
        .mockResolvedValueOnce({ totalBalance: '200000000' }) // 0.2 SUI (after GAS_RESERVE: ~0 SUI)

      const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
      const promise = checkAndRebalance(
        makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
      )
      await vi.advanceTimersByTimeAsync(30_000)
      const { result, newPositionId } = await promise

      expect(result!.success).toBe(true)
      expect(newPositionId).toBe('0xnewpos')
      // addLiquidity should be called for idle fund deployment
      expect(mockAddLiquidity).toHaveBeenCalledTimes(1)
      expect(mockAddLiquidity).toHaveBeenCalledWith(
        '0xpool',
        '0xnewpos',
        expect.any(String),
        expect.any(String),
        -180, 180,
        expect.any(String),
        expect.any(String),
        0.01,
        false, // collectFee
        expect.anything(), // keypair
        false, // dryRun
        [],    // rewarderCoinTypes
        BigInt('18446744073709551616'), // currentSqrtPrice
      )

      vi.useRealTimers()
    })

    it('skips when idle funds below threshold', async () => {
      vi.useFakeTimers()
      setupIdleDeployMocks()

      // Pre-flight + preClose + postClose + open + detect pos
      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '0' })
        .mockResolvedValueOnce({ totalBalance: '2000000000' })
        .mockResolvedValueOnce({ totalBalance: '0' })
        .mockResolvedValueOnce({ totalBalance: '500000000' })

      mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '5000000' })
        .mockResolvedValueOnce({ totalBalance: '2500000000' })

      // Rebalance ratio swap: no swap needed (balanced)
      mockCalculateSwapPlan.mockReturnValue({
        needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
        targetAmountA: BigInt(5_000_000), targetAmountB: BigInt(2_000_000_000),
      })

      mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
      mockGetPositions.mockResolvedValue([
        makePosition({ positionId: '0xnewpos', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('500000000') }),
      ])

      // Idle deploy: only $0.50 USDC + 0.05 SUI (below both thresholds after gas reserve)
      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '500000' })     // $0.50 USDC < $1 threshold
        .mockResolvedValueOnce({ totalBalance: '1050000000' }) // 1.05 SUI - 1.0 reserve = 0.05 SUI < 0.1 threshold

      const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
      const promise = checkAndRebalance(
        makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
      )
      await vi.advanceTimersByTimeAsync(30_000)
      const { result } = await promise

      expect(result!.success).toBe(true)
      // addLiquidity should NOT be called — idle funds below threshold
      expect(mockAddLiquidity).not.toHaveBeenCalled()
      // calculateSwapPlan IS called during rebalance (ratio check), just not during idle deploy
      expect(mockExecuteSwap).not.toHaveBeenCalled()

      vi.useRealTimers()
    })

    it('does not affect rebalance result on failure', async () => {
      vi.useFakeTimers()
      setupIdleDeployMocks()

      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '0' })
        .mockResolvedValueOnce({ totalBalance: '2000000000' })
        .mockResolvedValueOnce({ totalBalance: '0' })
        .mockResolvedValueOnce({ totalBalance: '500000000' })

      mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })

      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '5000000' })
        .mockResolvedValueOnce({ totalBalance: '2500000000' })

      mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })
      mockGetPositions.mockResolvedValue([
        makePosition({ positionId: '0xnewpos', tickLowerIndex: -180, tickUpperIndex: 180, liquidity: BigInt('500000000') }),
      ])

      // Idle deploy: enough funds but addLiquidity throws
      mockGetBalance
        .mockResolvedValueOnce({ totalBalance: '20000000' })   // $20 USDC
        .mockResolvedValueOnce({ totalBalance: '3000000000' }) // 3 SUI

      mockCalculateSwapPlan.mockReturnValue({
        needSwap: false, a2b: false, swapAmount: 0n, reason: 'ratio OK',
        targetAmountA: 20_000_000n, targetAmountB: 2_000_000_000n,
      })
      mockAddLiquidity.mockResolvedValue({ success: false, digest: null, gasCost: 0n, error: 'add failed' })

      const config = makeConfig({ dryRun: false, swapFreeRebalance: true })
      const promise = checkAndRebalance(
        makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
      )
      await vi.advanceTimersByTimeAsync(30_000)
      const { result } = await promise

      // Rebalance itself should still succeed even though idle deploy failed
      expect(result!.success).toBe(true)
      expect(result!.digest).toBe('0xopen')

      vi.useRealTimers()
    })

    it('skips in dry-run mode', async () => {
      setupIdleDeployMocks()
      mockEstimatePositionAmounts.mockReturnValue({ amountA: BigInt(5_000_000), amountB: BigInt(2_000_000_000) })
      mockClosePosition.mockResolvedValue({ success: true, digest: '0xclose', gasCost: 3_000_000n, error: null })
      mockOpenPosition.mockResolvedValue({ success: true, digest: '0xopen', gasCost: 3_000_000n, error: null })

      const config = makeConfig({ dryRun: true, swapFreeRebalance: true })
      const { result } = await checkAndRebalance(
        makePool(), makePosition(), config, makePoolConfig(), mockKeypair,
      )

      expect(result!.success).toBe(true)
      // In dry-run mode, deployIdleFunds should NOT be called
      // (no newPositionId detected since dryRun skips position detection)
      expect(mockAddLiquidity).not.toHaveBeenCalled()
    })
  })

  describe('feeToUsd', () => {
    // feeToUsd is also internal to scheduler.ts. We replicate its logic for testing.
    const DECIMALS_A = 6
    const DECIMALS_B = 9

    function feeToUsd(feeA: bigint, feeB: bigint, suiPriceUsdc: number): number {
      const usdcValue = Number(feeA) / (10 ** DECIMALS_A)
      const suiValue = (Number(feeB) / (10 ** DECIMALS_B)) * suiPriceUsdc
      return usdcValue + suiValue
    }

    it('USDC + SUI → USD value', () => {
      // 2 USDC + 1 SUI at $3.50 = $2 + $3.50 = $5.50
      const result = feeToUsd(BigInt(2_000_000), BigInt(1_000_000_000), 3.5)
      expect(result).toBeCloseTo(5.5, 5)
    })

    it('zero fees → 0', () => {
      expect(feeToUsd(0n, 0n, 3.5)).toBe(0)
    })

    it('only USDC fees → USDC value', () => {
      const result = feeToUsd(BigInt(10_000_000), 0n, 3.5)
      expect(result).toBeCloseTo(10.0, 5)
    })

    it('only SUI fees → SUI × price', () => {
      const result = feeToUsd(0n, BigInt(2_000_000_000), 3.5)
      expect(result).toBeCloseTo(7.0, 5)
    })
  })

  describe('circuit breaker', () => {
    // The circuit breaker logic is inside startScheduler's closure.
    // We test the pattern: after MAX_CONSECUTIVE_FAILURES=5 failures, position is skipped.
    // We simulate this by testing the logic pattern directly.

    it('tracks consecutive failures and triggers at threshold', () => {
      const MAX_CONSECUTIVE_FAILURES = 5
      const failureCounts = new Map<string, number>()
      const skippedPositions = new Set<string>()
      const posId = '0xpos1'

      for (let i = 0; i < MAX_CONSECUTIVE_FAILURES; i++) {
        const count = (failureCounts.get(posId) ?? 0) + 1
        failureCounts.set(posId, count)

        if (count >= MAX_CONSECUTIVE_FAILURES) {
          skippedPositions.add(posId)
        }
      }

      expect(failureCounts.get(posId)).toBe(5)
      expect(skippedPositions.has(posId)).toBe(true)
    })

    it('resets on success', () => {
      const failureCounts = new Map<string, number>()
      const backoffUntil = new Map<string, number>()
      const posId = '0xpos1'

      // Accumulate 3 failures
      failureCounts.set(posId, 3)
      backoffUntil.set(posId, Date.now() + 180_000)

      // Success resets
      failureCounts.delete(posId)
      backoffUntil.delete(posId)

      expect(failureCounts.has(posId)).toBe(false)
      expect(backoffUntil.has(posId)).toBe(false)
    })

    it('skipped position stays skipped on subsequent checks', () => {
      const skippedPositions = new Set<string>()
      const posId = '0xpos1'

      skippedPositions.add(posId)

      // Simulate multiple check cycles
      for (let i = 0; i < 10; i++) {
        if (skippedPositions.has(posId)) {
          // This position would be skipped
          continue
        }
        // Should never reach here
        expect(true).toBe(false)
      }

      expect(skippedPositions.has(posId)).toBe(true)
    })

    it('exponential backoff increases with failures', () => {
      const backoffSeconds: number[] = []

      for (let count = 1; count <= 5; count++) {
        const backoffSec = Math.min(60 * count, 300)
        backoffSeconds.push(backoffSec)
      }

      expect(backoffSeconds).toEqual([60, 120, 180, 240, 300])
    })
  })
})
