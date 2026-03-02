import type { PoolInfo, PositionInfo, RebalanceDecision, TriggerType } from '../types/index.js'
import { tickToPrice, getCurrentPrice } from '../core/price.js'
import { getLogger } from '../utils/logger.js'
import { loadDailyRebalanceCounts, saveDailyRebalanceCount } from '../utils/state.js'

/** Cooldown after SUI下落 (pool tick上昇, position is SUI heavy).
 *  Longer cooldown to wait for potential SUI rebound before rebalancing (avoid selling low). */
const COOLDOWN_UP_SEC = 3600 // 60 minutes

/** Cooldown after SUI上昇 (pool tick下降, position is USDC heavy).
 *  Shorter cooldown: re-enter LP quickly to benefit from continued SUI upside. */
const COOLDOWN_DOWN_SEC = 1800 // 30 minutes

/** Tracks the direction of the most recent range-out for each position.
 *  Used to apply asymmetric cooldown even after a rebalance completes. */
const rangeOutDirection = new Map<string, 'up' | 'down'>()

/** Tracks when range-out was first detected for each position (wait-before-rebalance).
 *  Cleared after the wait period elapses and rebalance proceeds. */
const rangeOutDetectedAt = new Map<string, number>()

/** Tracks when range-out was FIRST detected (persistent across wait period).
 *  Used for profitability gate time-decay bypass — cleared only when price
 *  returns to range and stays there past the grace period. */
const rangeOutFirstDetectedAt = new Map<string, number>()

/** Tracks when price re-entered range after a range-out (grace period start).
 *  During the grace period, rangeOutDirection/rangeOutFirstDetectedAt are preserved
 *  so threshold triggers can bypass minTimeInRangeSec if price just recovered. */
const rangeOutReEntryTime = new Map<string, number>()

/** Grace period after price re-enters range before clearing range-out state (seconds).
 *  Prevents premature clearing when price oscillates around the range boundary. */
const RANGE_REENTRY_GRACE_SEC = 600 // 10 minutes

/** Extended range-out duration after which profitability gate is bypassed (seconds).
 *  Ensures position is not stuck out-of-range indefinitely due to conservative
 *  breakeven estimates from the fallback formula. */
const PROFITABILITY_BYPASS_SEC = 7200 // 2 hours

/** Tracks daily rebalance counts per position (UTC date key). */
const dailyRebalanceCount = new Map<string, number>()
const dailyRebalanceDate = new Map<string, string>()

/** Tracks last range-fit execution time per position */
const rangeFitLastTime = new Map<string, number>()

/** Range-fit cooldown (6 hours) — separate from range-out cooldown */
const RANGE_FIT_COOLDOWN_SEC = 21600

/** Minimum ratio of current/optimal width to trigger range-fit */
const RANGE_FIT_RATIO = 2.0

/** Max breakeven hours for range-fit narrowing */
const RANGE_FIT_BREAKEVEN_MAX_HOURS = 12

/** Required consecutive stable volatility readings */
const RANGE_FIT_VOL_STABILITY_MIN = 3

export interface TriggerParams {
  rebalanceThreshold: number
  decimalsA: number
  decimalsB: number
  lastRebalanceTime?: number
  timeBasedIntervalSec?: number
  /** Pool swap fee rate (e.g. 0.0025 for 0.25%) — used for profitability gate */
  poolFeeRate?: number
  /**
   * Observed hourly fee accrual in USD value.
   * When provided, replaces the hardcoded estimation model with real data.
   */
  observedHourlyFeeUsd?: number
  /**
   * Position total value in USD.
   * Used with poolFeeRate to estimate swap cost of rebalance.
   */
  positionValueUsd?: number
  /** Seconds to wait after range-out before rebalancing (default: 1800 = 30min) */
  waitAfterRangeoutSec?: number
  /** Max rebalances per day per position (default: 3) */
  maxRebalancesPerDay?: number
  /** Minimum seconds in range before threshold trigger is allowed (default: 7200 = 2h) */
  minTimeInRangeSec?: number
  /** Timestamp when the current position was opened (for minTimeInRange guard) */
  positionOpenedAt?: number
  /** Current optimal tick width from volatility engine (undefined = no data) */
  optimalTickWidth?: number
  /** Number of consecutive volatility readings at the same tier (stability check) */
  volStabilityCount?: number
  /** Daily volume ratio assumption for fallback breakeven model (default: 0.02) */
  fallbackDailyVolumeRatio?: number
  /** Maximum breakeven hours for profitability gate (default: 48) */
  maxBreakevenHours?: number
}

/**
 * Estimate breakeven hours using the hardcoded model (fallback).
 * Used only when no observed fee data is available.
 */
function estimateBreakevenHoursFallback(
  poolFeeRate: number,
  rangeWidthPct: number,
  dailyVolumeRatio?: number,
): number {
  const volumeRatio = dailyVolumeRatio ?? 0.02
  const capitalEfficiency = 1 / (2 * rangeWidthPct)
  const swapCostPct = poolFeeRate * 0.5
  const dailyFeeRatePct = poolFeeRate * volumeRatio * capitalEfficiency
  const hourlyFeeRatePct = dailyFeeRatePct / 24
  if (hourlyFeeRatePct <= 0) return Infinity
  return swapCostPct / hourlyFeeRatePct
}

/**
 * Calculate breakeven hours from observed (real) fee data.
 *
 *   breakeven = swapCost / hourlyFee
 *   swapCost  = positionValue × poolFeeRate × 0.5  (assume ~50% needs swapping)
 *   hourlyFee = observedHourlyFeeUsd (measured from on-chain fee accrual)
 */
function calculateBreakevenHours(
  poolFeeRate: number,
  positionValueUsd: number,
  observedHourlyFeeUsd: number,
): number {
  if (observedHourlyFeeUsd <= 0) return Infinity
  const swapCostUsd = positionValueUsd * poolFeeRate * 0.5
  return swapCostUsd / observedHourlyFeeUsd
}

/** Get current UTC date string (YYYY-MM-DD) for daily rebalance tracking */
function utcDateKey(): string {
  return new Date().toISOString().slice(0, 10)
}

/**
 * Load persisted daily rebalance counts from state.json into in-memory maps.
 * Call once at startup so daily limits survive service restarts.
 */
export function initDailyRebalanceCounts(): void {
  const log = getLogger()
  const persisted = loadDailyRebalanceCounts()
  const today = utcDateKey()
  let restored = 0
  for (const [posId, entry] of Object.entries(persisted)) {
    if (entry.date === today) {
      dailyRebalanceCount.set(posId, entry.count)
      dailyRebalanceDate.set(posId, entry.date)
      restored++
    }
  }
  if (restored > 0) {
    log.info('Restored daily rebalance counts from state.json', { restored })
  }
}

/** Record a rebalance for daily counting (in-memory + persisted to state.json).
 *  When claimableUsd >= freeHarvestThreshold, the rebalance is "free" and does
 *  not count toward the daily limit (the fees being harvested justify the cost). */
export function recordRebalanceForDay(
  positionId: string,
  claimableUsd?: number,
  freeHarvestThreshold?: number,
): void {
  const log = getLogger()
  const threshold = freeHarvestThreshold ?? 3.0

  if (claimableUsd != null && isFinite(claimableUsd) && claimableUsd >= threshold) {
    log.info('Rebalance recorded as FREE (claimable fees above threshold, not counted toward daily limit)', {
      positionId,
      claimableUsd: claimableUsd.toFixed(4),
      freeHarvestThreshold: threshold,
    })
    return
  }

  const today = utcDateKey()
  const lastDate = dailyRebalanceDate.get(positionId)
  let newCount: number
  if (lastDate !== today) {
    newCount = 1
    dailyRebalanceCount.set(positionId, newCount)
    dailyRebalanceDate.set(positionId, today)
  } else {
    newCount = (dailyRebalanceCount.get(positionId) ?? 0) + 1
    dailyRebalanceCount.set(positionId, newCount)
  }
  saveDailyRebalanceCount(positionId, today, newCount)
}

/** Transfer daily rebalance tracking state when position ID changes */
export function transferDailyState(oldId: string, newId: string): void {
  const count = dailyRebalanceCount.get(oldId)
  const date = dailyRebalanceDate.get(oldId)
  if (count != null && date != null) {
    dailyRebalanceCount.set(newId, count)
    dailyRebalanceDate.set(newId, date)
    dailyRebalanceCount.delete(oldId)
    dailyRebalanceDate.delete(oldId)
  }
  const fitTime = rangeFitLastTime.get(oldId)
  if (fitTime != null) {
    rangeFitLastTime.set(newId, fitTime)
    rangeFitLastTime.delete(oldId)
  }
  // Clear range-out tracking state after successful rebalance
  // (new position has a fresh range, old timestamps are stale)
  rangeOutFirstDetectedAt.delete(oldId)
  rangeOutReEntryTime.delete(oldId)
  rangeOutDirection.delete(oldId)
  rangeOutDetectedAt.delete(oldId)
}

/**
 * Validate that pool config does not create a profitability gate deadlock.
 * A deadlock occurs when the minimum tick width from the dynamic strategy
 * always produces a breakeven estimate exceeding the profitability gate limit,
 * effectively preventing all rebalances (except after the 2h bypass timer).
 *
 * Call at startup to catch configuration conflicts early.
 */
export function validateProfitabilityGateConfig(
  poolFeeRate: number,
  tickWidthMin: number,
  tickSpacing: number,
  maxBreakevenHoursOverride?: number,
  dailyVolumeRatio?: number,
): { ok: boolean; breakevenHours: number; message: string } {
  const log = getLogger()
  const maxBreakevenHours = maxBreakevenHoursOverride ?? 48

  // Approximate rangeWidthPct from tick width:
  //   tickWidth ticks ≈ tickWidth × 0.01% price change (each tick ≈ 1 bps)
  //   rangeWidthPct ≈ tickWidth × 0.0001
  const alignedMin = Math.max(Math.floor(tickWidthMin / tickSpacing), 1) * tickSpacing
  const rangeWidthPct = alignedMin * 0.0001

  const breakevenHours = estimateBreakevenHoursFallback(poolFeeRate, rangeWidthPct, dailyVolumeRatio)

  if (breakevenHours > maxBreakevenHours) {
    const message = `Config conflict: volTickWidthMin=${tickWidthMin} (aligned=${alignedMin}) with poolFeeRate=${poolFeeRate} ` +
      `produces fallback breakeven=${breakevenHours.toFixed(1)}h > ${maxBreakevenHours}h limit. ` +
      `Rebalance will be blocked by profitability gate until 2h bypass timer. ` +
      `Consider increasing volTickWidthMin or adjusting FALLBACK_DAILY_VOLUME_RATIO.`
    log.warn('Profitability gate config validation FAILED', {
      tickWidthMin,
      alignedMin,
      poolFeeRate,
      dailyVolumeRatio: dailyVolumeRatio ?? 0.02,
      rangeWidthPct: rangeWidthPct.toFixed(4),
      breakevenHours: breakevenHours.toFixed(1),
      maxBreakevenHours,
    })
    return { ok: false, breakevenHours, message }
  }

  return { ok: true, breakevenHours, message: 'OK' }
}

export function evaluateRebalanceTrigger(
  pool: PoolInfo,
  position: PositionInfo,
  params: TriggerParams,
): RebalanceDecision {
  const log = getLogger()
  const { rebalanceThreshold, decimalsA, decimalsB } = params

  const currentPrice = getCurrentPrice(pool, decimalsA, decimalsB)
  const lowerPrice = tickToPrice(position.tickLowerIndex, decimalsA, decimalsB)
  const upperPrice = tickToPrice(position.tickUpperIndex, decimalsA, decimalsB)
  const rangeWidth = upperPrice - lowerPrice
  const midPrice = (upperPrice + lowerPrice) / 2
  const rangeWidthPct = rangeWidth / midPrice

  const base: Omit<RebalanceDecision, 'shouldRebalance' | 'trigger' | 'newLower' | 'newUpper' | 'reason'> = {
    currentPrice,
    currentLower: lowerPrice,
    currentUpper: upperPrice,
  }

  // Determine range-out direction (if any) for cooldown selection
  const isBelow = currentPrice <= lowerPrice
  const isAbove = currentPrice >= upperPrice
  const isOutOfRange = isBelow || isAbove

  // Asymmetric cooldown: longer for SUI下落 (SUI heavy → wait for rebound), shorter for SUI上昇 (USDC heavy → re-enter quickly)
  if (params.lastRebalanceTime) {
    const elapsedSec = (Date.now() - params.lastRebalanceTime) / 1000
    const lastDirection = rangeOutDirection.get(position.positionId)

    // Select cooldown based on current or last-known direction
    // SUI下落 = pool tick上昇 = position is SUI heavy → long cooldown (60min)
    // SUI上昇 = pool tick下降 = position is USDC heavy → short cooldown (30min)
    let cooldownSec: number
    let cooldownReason: string
    if (isBelow || lastDirection === 'down') {
      cooldownSec = COOLDOWN_DOWN_SEC
      cooldownReason = 'SUI上昇 (short cooldown, re-enter quickly)'
    } else {
      cooldownSec = COOLDOWN_UP_SEC
      cooldownReason = 'SUI下落 (long cooldown, waiting for rebound)'
    }

    if (elapsedSec < cooldownSec) {
      // Cooldown: prevents consecutive rebalances from being triggered too quickly.
      // Distinct from waitAfterRangeoutSec which delays initial range-out response
      // to allow price self-correction.
      const remainingSec = Math.ceil(cooldownSec - elapsedSec)
      log.info('Rebalance cooldown active', {
        elapsedSec: Math.floor(elapsedSec),
        cooldownSec,
        remainingSec,
        direction: cooldownReason,
        isOutOfRange,
      })
      return {
        ...base,
        shouldRebalance: false,
        trigger: null,
        newLower: null,
        newUpper: null,
        reason: `Cooldown (${cooldownReason}): ${Math.floor(elapsedSec)}s / ${cooldownSec}s`,
      }
    }
  }

  // Check 0: Recovery — 0-liquidity position (interrupted rebalance)
  // Always rebalance immediately: no fees are accruing, wallet funds are idle
  if (position.liquidity === 0n) {
    log.warn('Position has 0 liquidity (recovery mode)', { positionId: position.positionId })
    return {
      ...base,
      shouldRebalance: true,
      trigger: 'range-out' as TriggerType,
      newLower: null,
      newUpper: null,
      reason: 'Recovery: position has 0 liquidity (previous rebalance interrupted)',
    }
  }

  // Daily rebalance limit check (soft-limit: range-out bypasses)
  const maxPerDay = params.maxRebalancesPerDay ?? 3
  const today = utcDateKey()
  const lastDate = dailyRebalanceDate.get(position.positionId)
  const todayCount = lastDate === today ? (dailyRebalanceCount.get(position.positionId) ?? 0) : 0
  const dailyLimitReached = todayCount >= maxPerDay
  if (dailyLimitReached && !isOutOfRange) {
    // Soft-limit: only block threshold/time-based/range-fit triggers.
    // Range-out (emergency) triggers are allowed to bypass the daily limit.
    log.info('Daily rebalance limit reached (soft-limit, non-emergency)', {
      positionId: position.positionId,
      count: todayCount,
      limit: maxPerDay,
    })
    return {
      ...base,
      shouldRebalance: false,
      trigger: null,
      newLower: null,
      newUpper: null,
      reason: `Daily rebalance limit reached (${todayCount}/${maxPerDay}) — threshold/fit triggers blocked`,
    }
  }
  if (dailyLimitReached && isOutOfRange) {
    log.info('Daily rebalance limit reached but range-out detected — allowing emergency rebalance', {
      positionId: position.positionId,
      count: todayCount,
      limit: maxPerDay,
    })
  }

  // Check 1: Range out — price outside position range
  if (isOutOfRange) {
    const trigger: TriggerType = 'range-out'
    const direction = isBelow ? 'down' : 'up'

    // Track direction for future cooldown decisions
    rangeOutDirection.set(position.positionId, direction)

    // Track first detection time for profitability gate bypass (only set once)
    if (!rangeOutFirstDetectedAt.has(position.positionId)) {
      rangeOutFirstDetectedAt.set(position.positionId, Date.now())
    }
    // If price had re-entered range (grace period was active) but went out again,
    // reset the wait timer so it restarts from now rather than continuing from the old timestamp.
    if (rangeOutReEntryTime.has(position.positionId)) {
      rangeOutDetectedAt.delete(position.positionId)
      rangeOutReEntryTime.delete(position.positionId)
    }

    log.warn('Price out of range', { currentPrice, lowerPrice, upperPrice, direction })

    // Range-out wait: delay rebalance to allow price self-correction
    const waitSec = params.waitAfterRangeoutSec ?? 1800
    if (waitSec > 0) {
      const detectedAt = rangeOutDetectedAt.get(position.positionId)
      if (detectedAt == null) {
        // First detection — record timestamp and skip
        rangeOutDetectedAt.set(position.positionId, Date.now())
        log.info('Range-out detected, starting wait period', {
          positionId: position.positionId,
          waitSec,
          direction,
        })
        return {
          ...base,
          shouldRebalance: false,
          trigger: null,
          newLower: null,
          newUpper: null,
          reason: `Range-out (${direction}) detected, waiting ${waitSec}s before rebalance`,
        }
      }
      const waitedSec = (Date.now() - detectedAt) / 1000
      if (waitedSec < waitSec) {
        log.debug('Range-out wait period active', {
          waitedSec: Math.floor(waitedSec),
          waitSec,
          direction,
        })
        return {
          ...base,
          shouldRebalance: false,
          trigger: null,
          newLower: null,
          newUpper: null,
          reason: `Range-out (${direction}) wait: ${Math.floor(waitedSec)}s / ${waitSec}s`,
        }
      }
      // Wait period elapsed — clear and proceed
      rangeOutDetectedAt.delete(position.positionId)
    }

    // Profitability gate
    if (params.poolFeeRate) {
      const maxBreakevenHours = params.maxBreakevenHours ?? 48

      let breakevenHours: number
      let dataSource: string

      if (params.observedHourlyFeeUsd != null && params.observedHourlyFeeUsd > 0 && params.positionValueUsd != null) {
        breakevenHours = calculateBreakevenHours(
          params.poolFeeRate,
          params.positionValueUsd,
          params.observedHourlyFeeUsd,
        )
        dataSource = 'observed'
      } else {
        breakevenHours = estimateBreakevenHoursFallback(params.poolFeeRate, rangeWidthPct, params.fallbackDailyVolumeRatio)
        dataSource = 'estimated'
      }

      log.info('Profitability gate', {
        breakevenHours: breakevenHours.toFixed(1),
        maxBreakevenHours,
        dataSource,
        direction,
        observedHourlyFeeUsd: params.observedHourlyFeeUsd?.toFixed(4),
        positionValueUsd: params.positionValueUsd?.toFixed(2),
      })

      if (breakevenHours > maxBreakevenHours) {
        // Time-decay bypass: if position has been out of range for an extended period,
        // override the profitability gate to avoid being stuck indefinitely
        const firstDetected = rangeOutFirstDetectedAt.get(position.positionId)
        const rangeOutDurationSec = firstDetected ? (Date.now() - firstDetected) / 1000 : 0

        if (rangeOutDurationSec >= PROFITABILITY_BYPASS_SEC) {
          log.warn('Profitability gate bypassed — extended range-out override', {
            breakevenHours: breakevenHours.toFixed(1),
            maxBreakevenHours,
            rangeOutDurationSec: Math.floor(rangeOutDurationSec),
            bypassThresholdSec: PROFITABILITY_BYPASS_SEC,
            dataSource,
          })
          // Fall through — allow rebalance
        } else {
          return {
            ...base,
            shouldRebalance: false,
            trigger: null,
            newLower: null,
            newUpper: null,
            reason: `Range-out (${direction}) but breakeven ${breakevenHours.toFixed(0)}h > ${maxBreakevenHours}h limit (${dataSource} data) — BLOCKED (${dataSource === 'estimated' ? 'deterministic fallback, will not resolve by waiting' : 'need higher fee accrual'}) (${Math.floor(rangeOutDurationSec)}s / ${PROFITABILITY_BYPASS_SEC}s bypass)`,
          }
        }
      }
    }

    // Downward range-out: log the asymmetric delay rationale
    if (isBelow) {
      log.info('Downward range-out — proceeding after extended cooldown', {
        cooldownApplied: `${COOLDOWN_DOWN_SEC}s`,
        rationale: 'Price did not recover into range during wait period',
      })
    }

    return {
      ...base,
      shouldRebalance: true,
      trigger,
      newLower: null,
      newUpper: null,
      reason: `Price ${currentPrice.toFixed(6)} is outside range [${lowerPrice.toFixed(6)}, ${upperPrice.toFixed(6)}] (${direction})`,
    }
  }

  // Price is back in range — apply grace period before clearing range-out state.
  // This prevents premature clearing when price oscillates around the boundary,
  // and allows threshold trigger to bypass minTimeInRangeSec during grace period.
  if (rangeOutDirection.has(position.positionId)) {
    const reEnteredAt = rangeOutReEntryTime.get(position.positionId)
    if (!reEnteredAt) {
      rangeOutReEntryTime.set(position.positionId, Date.now())
      log.debug('Price re-entered range, starting grace period', {
        positionId: position.positionId,
        graceSec: RANGE_REENTRY_GRACE_SEC,
      })
    }
    const reEntryElapsed = reEnteredAt ? (Date.now() - reEnteredAt) / 1000 : 0
    if (reEntryElapsed > RANGE_REENTRY_GRACE_SEC) {
      rangeOutDetectedAt.delete(position.positionId)
      rangeOutDirection.delete(position.positionId)
      rangeOutReEntryTime.delete(position.positionId)
      rangeOutFirstDetectedAt.delete(position.positionId)
      log.debug('Grace period elapsed, cleared range-out state', {
        positionId: position.positionId,
      })
    }
  } else {
    // No prior range-out state — ensure clean maps
    rangeOutDetectedAt.delete(position.positionId)
    rangeOutReEntryTime.delete(position.positionId)
    rangeOutFirstDetectedAt.delete(position.positionId)
  }

  // Check 2: Threshold — price approaching range edge
  const distToLower = currentPrice - lowerPrice
  const distToUpper = upperPrice - currentPrice
  const minDist = Math.min(distToLower, distToUpper)
  const distRatio = minDist / rangeWidth

  if (distRatio < rebalanceThreshold) {
    // Minimum time-in-range guard: don't threshold-rebalance too soon after opening.
    // Bypass if position recently recovered from range-out (grace period active) —
    // the position was already out-of-range so minTimeInRange is not meaningful.
    const recentlyOutOfRange = rangeOutDirection.has(position.positionId)
    const minTimeSec = params.minTimeInRangeSec ?? 7200
    if (!recentlyOutOfRange && params.positionOpenedAt != null) {
      const inRangeSec = (Date.now() - params.positionOpenedAt) / 1000
      if (inRangeSec < minTimeSec) {
        log.debug('Threshold trigger suppressed by minTimeInRange', {
          inRangeSec: Math.floor(inRangeSec),
          minTimeSec,
          distRatio,
        })
        return {
          ...base,
          shouldRebalance: false,
          trigger: null,
          newLower: null,
          newUpper: null,
          reason: `Threshold met but position too new (${Math.floor(inRangeSec)}s / ${minTimeSec}s min)`,
        }
      }
    }
    if (recentlyOutOfRange) {
      log.info('Threshold trigger: minTimeInRange bypassed (recent range-out recovery)', {
        positionId: position.positionId,
        distRatio,
      })
    }

    const trigger: TriggerType = 'threshold'
    const side = distToLower < distToUpper ? 'lower' : 'upper'
    log.info('Price near range edge', { distRatio, threshold: rebalanceThreshold, side })
    return {
      ...base,
      shouldRebalance: true,
      trigger,
      newLower: null,
      newUpper: null,
      reason: `Price within ${(distRatio * 100).toFixed(1)}% of ${side} edge (threshold: ${(rebalanceThreshold * 100).toFixed(1)}%)`,
    }
  }

  // Check 2.5: Range-fit — current range significantly wider than optimal
  if (params.optimalTickWidth != null) {
    const currentTickWidth = position.tickUpperIndex - position.tickLowerIndex
    const ratio = currentTickWidth / params.optimalTickWidth

    if (ratio >= RANGE_FIT_RATIO) {
      // Volatility stability check
      const volStability = params.volStabilityCount ?? 0
      if (volStability < RANGE_FIT_VOL_STABILITY_MIN) {
        log.debug('Range-fit: volatility not yet stable', { volStability, required: RANGE_FIT_VOL_STABILITY_MIN })
        // Fall through to time-based check
      } else {
        // Range-fit cooldown
        const lastFit = rangeFitLastTime.get(position.positionId)
        if (lastFit && (Date.now() - lastFit) / 1000 < RANGE_FIT_COOLDOWN_SEC) {
          log.debug('Range-fit cooldown active', {
            elapsedSec: Math.floor((Date.now() - lastFit) / 1000),
            cooldownSec: RANGE_FIT_COOLDOWN_SEC,
          })
          // Fall through
        } else {
          // minTimeInRange guard (reuse existing logic)
          const minTimeSec = params.minTimeInRangeSec ?? 7200
          if (params.positionOpenedAt != null) {
            const inRangeSec = (Date.now() - params.positionOpenedAt) / 1000
            if (inRangeSec < minTimeSec) {
              // Fall through — position too new
            } else {
              // Profitability gate: estimate breakeven for narrowing
              let shouldTrigger = true
              if (params.poolFeeRate && params.observedHourlyFeeUsd != null && params.observedHourlyFeeUsd > 0 && params.positionValueUsd) {
                const swapCostUsd = params.positionValueUsd * params.poolFeeRate * 0.5
                const estimatedNewHourlyFee = params.observedHourlyFeeUsd * ratio
                const feeImprovement = estimatedNewHourlyFee - params.observedHourlyFeeUsd
                const breakevenHours = feeImprovement > 0 ? swapCostUsd / feeImprovement : Infinity
                log.info('Range-fit profitability check', {
                  currentWidth: currentTickWidth,
                  optimalWidth: params.optimalTickWidth,
                  ratio: ratio.toFixed(1),
                  breakevenHours: breakevenHours.toFixed(1),
                  maxAllowed: RANGE_FIT_BREAKEVEN_MAX_HOURS,
                })
                if (breakevenHours > RANGE_FIT_BREAKEVEN_MAX_HOURS) {
                  shouldTrigger = false
                }
              }

              if (shouldTrigger) {
                rangeFitLastTime.set(position.positionId, Date.now())
                return {
                  ...base,
                  shouldRebalance: true,
                  trigger: 'range-fit' as TriggerType,
                  newLower: null,
                  newUpper: null,
                  reason: `Range-fit: current ${currentTickWidth}t is ${ratio.toFixed(1)}x wider than optimal ${params.optimalTickWidth}t`,
                }
              }
            }
          }
        }
      }
    }
  }

  // Check 3: Time-based rebalance
  if (params.lastRebalanceTime && params.timeBasedIntervalSec) {
    const elapsed = (Date.now() - params.lastRebalanceTime) / 1000
    if (elapsed >= params.timeBasedIntervalSec) {
      const trigger: TriggerType = 'time-based'
      log.info('Time-based rebalance triggered', { elapsedSec: elapsed })
      return {
        ...base,
        shouldRebalance: true,
        trigger,
        newLower: null,
        newUpper: null,
        reason: `Time-based: ${Math.floor(elapsed)}s since last rebalance (interval: ${params.timeBasedIntervalSec}s)`,
      }
    }
  }

  return {
    ...base,
    shouldRebalance: false,
    trigger: null,
    newLower: null,
    newUpper: null,
    reason: 'Price is within range and threshold',
  }
}
