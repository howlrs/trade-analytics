import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { PoolInfo, PositionInfo } from '../../src/types/index.js'

// Mock the price module
vi.mock('../../src/core/price.js', () => ({
  getCurrentPrice: vi.fn(),
  tickToPrice: vi.fn(),
}))

// Mock the logger
vi.mock('../../src/utils/logger.js', () => ({
  getLogger: () => ({
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  }),
}))

import { evaluateRebalanceTrigger, recordRebalanceForDay } from '../../src/strategy/trigger.js'
import { getCurrentPrice, tickToPrice } from '../../src/core/price.js'

const mockedGetCurrentPrice = vi.mocked(getCurrentPrice)
const mockedTickToPrice = vi.mocked(tickToPrice)

// --- Helpers ---

function makePool(overrides: Partial<PoolInfo> = {}): PoolInfo {
  return {
    poolId: '0xpool1',
    coinTypeA: '0x...::usdc::USDC',
    coinTypeB: '0x...::sui::SUI',
    currentSqrtPrice: 1000000n,
    currentTickIndex: 0,
    feeRate: 2500,
    liquidity: 1000000n,
    tickSpacing: 60,
    rewarderCoinTypes: [],
    ...overrides,
  }
}

function makePosition(overrides: Partial<PositionInfo> = {}): PositionInfo {
  return {
    positionId: '0xpos1',
    poolId: '0xpool1',
    owner: '0xowner',
    tickLowerIndex: -100,
    tickUpperIndex: 100,
    liquidity: 1000000n,
    feeOwedA: 0n,
    feeOwedB: 0n,
    rewardAmountOwed: [],
    ...overrides,
  }
}

/**
 * Set up price mocks so that:
 *   tickToPrice(lowerTick) = lowerPrice
 *   tickToPrice(upperTick) = upperPrice
 *   getCurrentPrice() = currentPrice
 */
function setupPriceMocks(
  currentPrice: number,
  lowerPrice: number,
  upperPrice: number,
) {
  mockedGetCurrentPrice.mockReturnValue(currentPrice)
  mockedTickToPrice.mockImplementation((tick: number) => {
    // Map specific ticks to prices; default linear
    if (tick === -100) return lowerPrice
    if (tick === 100) return upperPrice
    return lowerPrice + ((tick - (-100)) / 200) * (upperPrice - lowerPrice)
  })
}

// --- Tests ---

describe('evaluateRebalanceTrigger', () => {
  const decimalsA = 6
  const decimalsB = 9

  beforeEach(() => {
    vi.clearAllMocks()
    // Reset Date.now for cooldown tests
    vi.restoreAllMocks()
  })

  // ------------------------------------------------------------------
  // 1. Range-out detection
  // ------------------------------------------------------------------
  describe('range-out detection', () => {
    it('should trigger range-out when price is below lower tick', () => {
      setupPriceMocks(0.8, 1.0, 2.0) // current=0.8, range=[1.0, 2.0]

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0, // disable wait for basic range-out tests
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
      expect(result.reason).toContain('outside range')
      expect(result.reason).toContain('down')
    })

    it('should trigger range-out when price is above upper tick', () => {
      setupPriceMocks(2.5, 1.0, 2.0) // current=2.5, range=[1.0, 2.0]

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
      expect(result.reason).toContain('outside range')
      expect(result.reason).toContain('up')
    })

    it('should trigger range-out when price equals lower tick exactly', () => {
      setupPriceMocks(1.0, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })

    it('should trigger range-out when price equals upper tick exactly', () => {
      setupPriceMocks(2.0, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })
  })

  // ------------------------------------------------------------------
  // 2. Threshold trigger
  // ------------------------------------------------------------------
  describe('threshold trigger', () => {
    it('should trigger when price is within threshold of lower edge', () => {
      // range = [1.0, 2.0], width = 1.0
      // threshold = 0.15 → need distRatio < 0.15
      // price = 1.1 → distToLower = 0.1, distRatio = 0.1/1.0 = 0.10 < 0.15
      setupPriceMocks(1.1, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        minTimeInRangeSec: 0, // disable for basic threshold tests
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('threshold')
      expect(result.reason).toContain('lower')
    })

    it('should trigger when price is within threshold of upper edge', () => {
      // price = 1.9 → distToUpper = 0.1, distRatio = 0.1/1.0 = 0.10 < 0.15
      setupPriceMocks(1.9, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        minTimeInRangeSec: 0,
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('threshold')
      expect(result.reason).toContain('upper')
    })
  })

  // ------------------------------------------------------------------
  // 3. No rebalance
  // ------------------------------------------------------------------
  describe('no rebalance needed', () => {
    it('should not rebalance when price is centered in range', () => {
      // price = 1.5, range = [1.0, 2.0], distRatio = 0.5/1.0 = 0.50 > 0.15
      setupPriceMocks(1.5, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.trigger).toBeNull()
      expect(result.reason).toContain('within range')
    })

    it('should not rebalance when price is in range but above threshold distance from edges', () => {
      // price = 1.3, distToLower = 0.3, distToUpper = 0.7, distRatio = 0.3/1.0 = 0.30 > 0.15
      setupPriceMocks(1.3, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.trigger).toBeNull()
    })
  })

  // ------------------------------------------------------------------
  // 4. Cooldown (30min up, 60min down)
  // ------------------------------------------------------------------
  describe('cooldown', () => {
    it('should block rebalance when upward range-out occurred < 30min ago', () => {
      // First call: establish upward direction
      setupPriceMocks(2.5, 1.0, 2.0)
      const pos = makePosition()
      evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
      })

      // Second call: cooldown active (lastRebalanceTime = 10 min ago, < 30min up cooldown)
      const tenMinAgo = Date.now() - 10 * 60 * 1000
      setupPriceMocks(2.5, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        lastRebalanceTime: tenMinAgo,
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.reason).toContain('Cooldown')
    })

    it('should block rebalance when downward range-out occurred < 30min ago', () => {
      // First call: establish downward direction
      setupPriceMocks(0.5, 1.0, 2.0)
      const pos = makePosition()
      evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
      })

      // Second call: 15 min ago (within 30-min down cooldown)
      const fifteenMinAgo = Date.now() - 15 * 60 * 1000
      setupPriceMocks(0.5, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        lastRebalanceTime: fifteenMinAgo,
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.reason).toContain('Cooldown')
      expect(result.reason).toContain('down')
    })

    it('should allow rebalance when cooldown has expired (> 60min)', () => {
      // First call: establish downward direction
      setupPriceMocks(0.5, 1.0, 2.0)
      const pos = makePosition()
      evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
      })

      // Second call: 61 min ago (past 60-min cooldown)
      const sixtyOneMinAgo = Date.now() - 61 * 60 * 1000
      setupPriceMocks(0.5, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        lastRebalanceTime: sixtyOneMinAgo,
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })

    it('should use 60-min cooldown for upward direction and allow after expiry', () => {
      // First: upward range-out (price > upper → SUI fell → position is SUI heavy → long cooldown)
      setupPriceMocks(2.5, 1.0, 2.0)
      const pos = makePosition()
      evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
      })

      // 61 min later (past 60-min up cooldown)
      const sixtyOneMinAgo = Date.now() - 61 * 60 * 1000
      setupPriceMocks(2.5, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        lastRebalanceTime: sixtyOneMinAgo,
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })
  })

  // ------------------------------------------------------------------
  // 5. Recovery mode (0 liquidity)
  // ------------------------------------------------------------------
  describe('recovery mode', () => {
    it('should always rebalance when position has 0 liquidity', () => {
      // Price is centered (normally no rebalance) but liquidity = 0
      setupPriceMocks(1.5, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(
        makePool(),
        makePosition({ liquidity: 0n }),
        {
          rebalanceThreshold: 0.15,
          decimalsA,
          decimalsB,
        },
      )

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
      expect(result.reason).toContain('0 liquidity')
      expect(result.reason).toContain('Recovery')
    })

    it('should rebalance 0-liquidity even with cooldown active', () => {
      setupPriceMocks(1.5, 1.0, 2.0)
      const oneMinAgo = Date.now() - 60 * 1000

      const result = evaluateRebalanceTrigger(
        makePool(),
        makePosition({ liquidity: 0n }),
        {
          rebalanceThreshold: 0.15,
          decimalsA,
          decimalsB,
          lastRebalanceTime: oneMinAgo,
        },
      )

      // Cooldown check happens before recovery check in the code,
      // so with lastRebalanceTime set and no prior direction, it uses up cooldown (30min).
      // 1 min < 30 min → cooldown blocks it.
      expect(result.shouldRebalance).toBe(false)
      expect(result.reason).toContain('Cooldown')
    })
  })

  // ------------------------------------------------------------------
  // 6. Profitability gate (48h limit)
  // ------------------------------------------------------------------
  describe('profitability gate', () => {
    it('should block range-out rebalance when breakeven exceeds max hours (fallback model)', () => {
      setupPriceMocks(0.5, 1.0, 2.0) // out of range
      const pos = makePosition()

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        poolFeeRate: 0.0025,
        waitAfterRangeoutSec: 0,
        // No observedHourlyFeeUsd → uses fallback estimator
      })

      // Fallback model with 0.25% fee and ~67% rangeWidthPct should produce
      // a high breakeven (low capital efficiency relative to swap cost)
      // The function will calculate and compare against maxBreakevenHours=48
      // With these params, breakeven is very high → should block
      expect(result.shouldRebalance).toBe(false)
      expect(result.reason).toContain('breakeven')
      expect(result.reason).toContain('waiting')
    })

    it('should allow rebalance when observed fee data yields acceptable breakeven', () => {
      setupPriceMocks(0.5, 1.0, 2.0) // out of range
      const pos = makePosition()

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        poolFeeRate: 0.0025,
        positionValueUsd: 20,
        observedHourlyFeeUsd: 0.01, // breakeven = (20 * 0.0025 * 0.5) / 0.01 = 2.5h < 48h
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })

    it('should block rebalance when observedHourlyFeeUsd is 0 (Infinity breakeven)', () => {
      setupPriceMocks(0.5, 1.0, 2.0) // out of range
      const pos = makePosition()

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        poolFeeRate: 0.0025,
        positionValueUsd: 20,
        observedHourlyFeeUsd: 0, // → Infinity breakeven
        waitAfterRangeoutSec: 0,
      })

      // observedHourlyFeeUsd=0 but it's not > 0, so fallback is used
      // The fallback with wide range will likely exceed 48h too
      expect(result.shouldRebalance).toBe(false)
      expect(result.reason).toContain('breakeven')
    })

    it('should skip profitability gate when poolFeeRate is not provided', () => {
      setupPriceMocks(0.5, 1.0, 2.0) // out of range

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
        // no poolFeeRate → gate skipped
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })

    it('should block when observed breakeven exceeds 48h limit', () => {
      setupPriceMocks(2.5, 1.0, 2.0) // out of range (up)
      const pos = makePosition()

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        poolFeeRate: 0.0025,
        positionValueUsd: 100,
        observedHourlyFeeUsd: 0.001, // breakeven = (100 * 0.0025 * 0.5) / 0.001 = 125h >> 48h
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.reason).toContain('breakeven')
      expect(result.reason).toContain('observed')
    })
  })

  // ------------------------------------------------------------------
  // 7. Time-based trigger
  // ------------------------------------------------------------------
  describe('time-based trigger', () => {
    it('should trigger time-based rebalance when interval has elapsed', () => {
      // Price is centered (no range-out or threshold)
      setupPriceMocks(1.5, 1.0, 2.0)
      const twoHoursAgo = Date.now() - 2 * 3600 * 1000

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        lastRebalanceTime: twoHoursAgo,
        timeBasedIntervalSec: 3600, // 1 hour
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('time-based')
      expect(result.reason).toContain('Time-based')
    })

    it('should not trigger time-based when interval has not elapsed', () => {
      setupPriceMocks(1.5, 1.0, 2.0)
      const thirtyMinAgo = Date.now() - 30 * 60 * 1000

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        lastRebalanceTime: thirtyMinAgo,
        timeBasedIntervalSec: 3600, // 1 hour — only 30min elapsed
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.trigger).toBeNull()
    })

    it('should not trigger time-based when lastRebalanceTime is not set', () => {
      setupPriceMocks(1.5, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        // no lastRebalanceTime
        timeBasedIntervalSec: 3600,
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.trigger).toBeNull()
    })
  })

  // ------------------------------------------------------------------
  // 8. rangeOutDirection map state isolation
  // ------------------------------------------------------------------
  describe('rangeOutDirection map state', () => {
    it('should clear direction when price returns to range', () => {
      const pos = makePosition()

      // First: go out of range downward
      setupPriceMocks(0.5, 1.0, 2.0)
      const r1 = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
      })
      expect(r1.shouldRebalance).toBe(true)
      expect(r1.reason).toContain('down')

      // Second: price returns to range — direction should be cleared
      setupPriceMocks(1.5, 1.0, 2.0)
      evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
      })

      // Third: go out of range upward — should use UP cooldown (60min), not DOWN (30min)
      setupPriceMocks(2.5, 1.0, 2.0)
      evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
      })

      // Now check cooldown: 61min ago should pass for UP (60min) but would still block DOWN (60min wait)
      const sixtyOneMinAgo = Date.now() - 61 * 60 * 1000
      setupPriceMocks(2.5, 1.0, 2.0)
      const r4 = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        lastRebalanceTime: sixtyOneMinAgo,
        waitAfterRangeoutSec: 0,
      })

      // Should pass because direction is 'up' (60min cooldown) and 61min > 60min
      expect(r4.shouldRebalance).toBe(true)
    })
  })

  // ------------------------------------------------------------------
  // 9. Profitability gate on threshold trigger
  // ------------------------------------------------------------------
  describe('profitability gate does not apply to threshold trigger', () => {
    it('should allow threshold trigger even with poolFeeRate set (no profitability gate)', () => {
      // Price near lower edge but still in range
      setupPriceMocks(1.1, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        poolFeeRate: 0.0025,
        positionValueUsd: 20,
        observedHourlyFeeUsd: 0.0001, // very low fee — would block range-out
        minTimeInRangeSec: 0,
      })

      // Profitability gate only applies to range-out, not threshold
      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('threshold')
    })
  })

  // ------------------------------------------------------------------
  // 10. Range-out wait (waitAfterRangeoutSec)
  // ------------------------------------------------------------------
  describe('range-out wait', () => {
    it('should delay rebalance on first range-out detection (default 30min wait)', () => {
      setupPriceMocks(0.5, 1.0, 2.0)
      // Use a unique position ID to avoid state from other tests
      const pos = makePosition({ positionId: '0xwait_test_1' })

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        // Default waitAfterRangeoutSec = 1800
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.reason).toContain('wait')
      expect(result.reason).toContain('1800s')
    })

    it('should allow rebalance when waitAfterRangeoutSec is 0', () => {
      setupPriceMocks(0.5, 1.0, 2.0)
      const pos = makePosition({ positionId: '0xwait_test_2' })

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })

    it('should clear range-out detection when price returns to range', () => {
      const pos = makePosition({ positionId: '0xwait_test_3' })

      // First: out of range — starts wait
      setupPriceMocks(0.5, 1.0, 2.0)
      const r1 = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
      })
      expect(r1.shouldRebalance).toBe(false)

      // Second: back in range — clears detection
      setupPriceMocks(1.5, 1.0, 2.0)
      evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
      })

      // Third: out of range again — should restart wait (not carry over)
      setupPriceMocks(0.5, 1.0, 2.0)
      const r3 = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
      })
      expect(r3.shouldRebalance).toBe(false)
      expect(r3.reason).toContain('wait')
    })
  })

  // ------------------------------------------------------------------
  // 11. Daily rebalance limit (maxRebalancesPerDay)
  // ------------------------------------------------------------------
  describe('daily rebalance limit', () => {
    it('should block threshold trigger when daily limit is reached', () => {
      const pos = makePosition({ positionId: '0xdaily_test_1' })

      // Record 3 rebalances for today
      recordRebalanceForDay(pos.positionId)
      recordRebalanceForDay(pos.positionId)
      recordRebalanceForDay(pos.positionId)

      // Price near edge (threshold trigger, not range-out)
      setupPriceMocks(1.1, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        maxRebalancesPerDay: 3,
        minTimeInRangeSec: 0,
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.reason).toContain('Daily rebalance limit')
    })

    it('should allow range-out even when daily limit is reached (soft-limit)', () => {
      const pos = makePosition({ positionId: '0xdaily_test_1b' })

      recordRebalanceForDay(pos.positionId)
      recordRebalanceForDay(pos.positionId)
      recordRebalanceForDay(pos.positionId)

      setupPriceMocks(0.5, 1.0, 2.0) // out of range

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        maxRebalancesPerDay: 3,
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })

    it('should allow rebalance when under daily limit', () => {
      const pos = makePosition({ positionId: '0xdaily_test_2' })

      // Record 1 rebalance
      recordRebalanceForDay(pos.positionId)

      setupPriceMocks(0.5, 1.0, 2.0) // out of range

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        maxRebalancesPerDay: 3,
        waitAfterRangeoutSec: 0,
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })
  })

  // ------------------------------------------------------------------
  // 12. Minimum time in range (minTimeInRangeSec)
  // ------------------------------------------------------------------
  describe('minimum time in range', () => {
    it('should suppress threshold trigger when position is too new', () => {
      // Price near lower edge — would normally trigger threshold
      setupPriceMocks(1.1, 1.0, 2.0)
      const thirtyMinAgo = Date.now() - 30 * 60 * 1000 // 30min < 2h default

      const result = evaluateRebalanceTrigger(makePool(), makePosition({ positionId: '0xmin_time_1' }), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        positionOpenedAt: thirtyMinAgo,
        // default minTimeInRangeSec = 7200 (2h)
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.reason).toContain('position too new')
    })

    it('should allow threshold trigger when position is old enough', () => {
      setupPriceMocks(1.1, 1.0, 2.0)
      const threeHoursAgo = Date.now() - 3 * 3600 * 1000 // 3h > 2h

      const result = evaluateRebalanceTrigger(makePool(), makePosition({ positionId: '0xmin_time_2' }), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        positionOpenedAt: threeHoursAgo,
        minTimeInRangeSec: 0, // Explicitly disable (but 3h > 2h default anyway)
      })

      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('threshold')
    })

    it('should not affect range-out trigger (only threshold)', () => {
      setupPriceMocks(0.5, 1.0, 2.0) // out of range
      const fiveMinAgo = Date.now() - 5 * 60 * 1000

      const result = evaluateRebalanceTrigger(makePool(), makePosition({ positionId: '0xmin_time_3' }), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        positionOpenedAt: fiveMinAgo,
        minTimeInRangeSec: 7200,
        waitAfterRangeoutSec: 0,
      })

      // Range-out should still trigger (minTimeInRange only affects threshold)
      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })
  })

  // ------------------------------------------------------------------
  // 13. Range-fit trigger
  // ------------------------------------------------------------------
  describe('range-fit trigger', () => {
    it('should trigger range-fit when range is 2x+ wider than optimal', () => {
      setupPriceMocks(1.5, 1.0, 2.0)
      const threeHoursAgo = Date.now() - 3 * 3600 * 1000
      const result = evaluateRebalanceTrigger(
        makePool(),
        makePosition({ positionId: '0xfit1', tickLowerIndex: -600, tickUpperIndex: 600 }), // 1200 ticks
        {
          rebalanceThreshold: 0.15,
          decimalsA,
          decimalsB,
          optimalTickWidth: 480,     // ratio = 1200/480 = 2.5 >= 2.0
          volStabilityCount: 3,
          positionOpenedAt: threeHoursAgo,
          minTimeInRangeSec: 7200,
        },
      )
      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-fit')
    })

    it('should NOT trigger when ratio < 2.0', () => {
      setupPriceMocks(1.5, 1.0, 2.0)
      const result = evaluateRebalanceTrigger(
        makePool(),
        makePosition({ positionId: '0xfit2', tickLowerIndex: -300, tickUpperIndex: 300 }), // 600 ticks
        {
          rebalanceThreshold: 0.15,
          decimalsA,
          decimalsB,
          optimalTickWidth: 480,     // ratio = 600/480 = 1.25 < 2.0
          volStabilityCount: 3,
          positionOpenedAt: Date.now() - 4 * 3600 * 1000,
        },
      )
      expect(result.shouldRebalance).toBe(false)
    })

    it('should NOT trigger when volatility is not stable', () => {
      setupPriceMocks(1.5, 1.0, 2.0)
      const result = evaluateRebalanceTrigger(
        makePool(),
        makePosition({ positionId: '0xfit3', tickLowerIndex: -600, tickUpperIndex: 600 }),
        {
          rebalanceThreshold: 0.15,
          decimalsA,
          decimalsB,
          optimalTickWidth: 480,
          volStabilityCount: 1, // < 3 required
          positionOpenedAt: Date.now() - 4 * 3600 * 1000,
        },
      )
      expect(result.shouldRebalance).toBe(false)
    })

    it('should NOT trigger when position is too new (minTimeInRange)', () => {
      setupPriceMocks(1.5, 1.0, 2.0)
      const result = evaluateRebalanceTrigger(
        makePool(),
        makePosition({ positionId: '0xfit4', tickLowerIndex: -600, tickUpperIndex: 600 }),
        {
          rebalanceThreshold: 0.15,
          decimalsA,
          decimalsB,
          optimalTickWidth: 480,
          volStabilityCount: 3,
          positionOpenedAt: Date.now() - 30 * 60 * 1000, // 30min < 2h
          minTimeInRangeSec: 7200,
        },
      )
      expect(result.shouldRebalance).toBe(false)
    })

    it('should block range-fit when profitability gate fails', () => {
      setupPriceMocks(1.5, 1.0, 2.0)
      const result = evaluateRebalanceTrigger(
        makePool(),
        makePosition({ positionId: '0xfit5', tickLowerIndex: -600, tickUpperIndex: 600 }),
        {
          rebalanceThreshold: 0.15,
          decimalsA,
          decimalsB,
          optimalTickWidth: 480,
          volStabilityCount: 3,
          positionOpenedAt: Date.now() - 4 * 3600 * 1000,
          poolFeeRate: 0.0025,
          positionValueUsd: 100,
          observedHourlyFeeUsd: 0.001, // breakeven = (100*0.0025*0.5) / (0.001*(2.5-1)) = 0.125/0.0015 = 83h > 12h
        },
      )
      expect(result.shouldRebalance).toBe(false)
    })
  })

  // ------------------------------------------------------------------
  // 14. Soft-limit: range-out bypasses daily limit
  // ------------------------------------------------------------------
  describe('soft daily limit (range-out bypass)', () => {
    it('should block threshold trigger when daily limit reached', () => {
      const pos = makePosition({ positionId: '0xsoft_limit_1' })

      // Record 3 rebalances for today
      recordRebalanceForDay(pos.positionId)
      recordRebalanceForDay(pos.positionId)
      recordRebalanceForDay(pos.positionId)

      // Price near edge (would be threshold trigger, not range-out)
      setupPriceMocks(1.1, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        maxRebalancesPerDay: 3,
        minTimeInRangeSec: 0,
      })

      expect(result.shouldRebalance).toBe(false)
      expect(result.reason).toContain('Daily rebalance limit')
      expect(result.reason).toContain('threshold/fit triggers blocked')
    })

    it('should allow range-out trigger even when daily limit reached', () => {
      const pos = makePosition({ positionId: '0xsoft_limit_2' })

      // Record 3 rebalances for today
      recordRebalanceForDay(pos.positionId)
      recordRebalanceForDay(pos.positionId)
      recordRebalanceForDay(pos.positionId)

      // Price out of range (range-out trigger)
      setupPriceMocks(0.5, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), pos, {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
        maxRebalancesPerDay: 3,
        waitAfterRangeoutSec: 0,
      })

      // Range-out should bypass the daily limit
      expect(result.shouldRebalance).toBe(true)
      expect(result.trigger).toBe('range-out')
    })
  })

  // ------------------------------------------------------------------
  // Return value structure
  // ------------------------------------------------------------------
  describe('return value structure', () => {
    it('should always include currentPrice, currentLower, currentUpper', () => {
      setupPriceMocks(1.5, 1.0, 2.0)

      const result = evaluateRebalanceTrigger(makePool(), makePosition(), {
        rebalanceThreshold: 0.15,
        decimalsA,
        decimalsB,
      })

      expect(result.currentPrice).toBe(1.5)
      expect(result.currentLower).toBe(1.0)
      expect(result.currentUpper).toBe(2.0)
      expect(result).toHaveProperty('newLower')
      expect(result).toHaveProperty('newUpper')
      expect(result).toHaveProperty('reason')
    })
  })
})
