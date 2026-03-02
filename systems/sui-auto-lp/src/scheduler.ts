import fs from 'node:fs'
import path from 'node:path'
import type { Config } from './types/config.js'
import type { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'
import { getPool } from './core/pool.js'
import { getPositions, fetchPositionFees } from './core/position.js'
import { checkAndRebalance, getLastRebalanceTime, initRebalanceTimes } from './core/rebalance.js'
import { initDailyRebalanceCounts, validateProfitabilityGateConfig } from './strategy/trigger.js'
import { checkAndHarvest } from './core/compound.js'
import { estimatePositionAmounts } from './core/price.js'
import { coinBPriceInCoinA } from './core/price.js'
import { calculateVolatilityBasedTicks } from './strategy/volatility.js'
import { detectRegime } from './strategy/regime.js'
import type { RegimeState } from './strategy/regime.js'
import { feeTracker } from './utils/fee-tracker.js'
import { savePositionId } from './utils/state.js'
import { getLogger } from './utils/logger.js'
import { recordEvent, closeEventLog } from './utils/event-log.js'

const DECIMALS_A = 6 // USDC
const DECIMALS_B = 9 // SUI

/** Minimum interval between fee fetches (seconds).
 *  Avoids unnecessary RPC calls on every 30s check cycle. */
const FEE_FETCH_INTERVAL_SEC = 120

/** Check PAUSED flag from .env at runtime (no restart needed). */
function isPaused(): boolean {
  try {
    const envPath = path.resolve(process.cwd(), '.env')
    const content = fs.readFileSync(envPath, 'utf-8')
    const match = content.match(/^PAUSED\s*=\s*(.+)$/m)
    return match ? match[1].trim().toLowerCase() === 'true' : false
  } catch {
    return false
  }
}

/**
 * Convert raw fee amounts to USD value using current SUI price.
 *   feeA is USDC (1:1 with USD)
 *   feeB is SUI (multiply by SUI price in USDC)
 */
function feeToUsd(feeA: bigint, feeB: bigint, suiPriceUsdc: number): number {
  const usdcValue = Number(feeA) / (10 ** DECIMALS_A)
  const suiValue = (Number(feeB) / (10 ** DECIMALS_B)) * suiPriceUsdc
  return usdcValue + suiValue
}

export function startScheduler(config: Config, keypair: Ed25519Keypair): () => void {
  const log = getLogger()
  const address = keypair.getPublicKey().toSuiAddress()

  let rebalanceTimer: ReturnType<typeof setInterval>
  let harvestTimer: ReturnType<typeof setTimeout>
  let running = false
  let lastFeeFetchTime = 0

  // Restore persisted rebalance times so minTimeInRangeSec guard
  // only applies after actual rebalances, not service restarts.
  initRebalanceTimes()
  // Restore daily rebalance counts so daily limits survive restarts.
  initDailyRebalanceCounts()

  // Validate profitability gate config for dynamic strategy pools.
  // Warns at startup if volTickWidthMin creates a breakeven deadlock.
  for (const poolConfig of config.pools) {
    if (poolConfig.strategy === 'dynamic') {
      // Pool fee rate is fetched later; use the common 0.25% fee tier as estimate.
      // The real validation happens per-cycle with actual pool data.
      const estimatedFeeRate = 0.0025
      const tickSpacing = 60 // common USDC/SUI pool spacing
      const result = validateProfitabilityGateConfig(
        estimatedFeeRate,
        poolConfig.volTickWidthMin,
        tickSpacing,
        config.maxBreakevenHours,
        config.fallbackDailyVolumeRatio,
      )
      if (!result.ok) {
        log.warn('STARTUP WARNING: ' + result.message)
      }
    }
  }

  // Circuit breaker: track consecutive failures per position
  const MAX_CONSECUTIVE_FAILURES = 5
  const failureCounts = new Map<string, number>()
  const backoffUntil = new Map<string, number>()  // positionId → Date.now() threshold
  const skippedPositions = new Set<string>()

  // Volatility cache for range-fit trigger (5-minute TTL)
  const VOL_CACHE_TTL = 300_000
  let volCache: { tickWidth: number; sigma: number; fetchedAt: number } | null = null
  const volHistory: number[] = []
  const sigmaHistory: number[] = []
  const VOL_HISTORY_MAX = 10
  const SIGMA_HISTORY_MAX = 24

  async function getOptimalTickWidth(
    poolId: string,
    tickSpacing: number,
    poolConfig: import('./types/config.js').PoolConfig,
  ): Promise<{ tickWidth: number; stabilityCount: number; sigma: number; regimeState: RegimeState } | null> {
    if (volCache && Date.now() - volCache.fetchedAt < VOL_CACHE_TTL) {
      const regimeState = poolConfig.regimeEnabled ? detectRegime(sigmaHistory) : { regime: 'mid' as const, isCompression: false, isTransition: false, currentSigma: volCache.sigma }
      return { tickWidth: volCache.tickWidth, stabilityCount: getStabilityCount(volCache.tickWidth), sigma: volCache.sigma, regimeState }
    }
    const result = await calculateVolatilityBasedTicks(
      poolId,
      tickSpacing,
      poolConfig.volLookbackHours,
      poolConfig.volTickWidthMin,
      poolConfig.volTickWidthMax,
      poolConfig.volScalingMode,
      poolConfig.sigmaLow,
      poolConfig.sigmaHigh,
      poolConfig.binanceVolFallback,
    )
    if (result) {
      volCache = { tickWidth: result.tickWidth, sigma: result.sigma, fetchedAt: Date.now() }
      volHistory.push(result.tickWidth)
      if (volHistory.length > VOL_HISTORY_MAX) volHistory.shift()
      // Track sigma history for regime detection
      if (result.sigma > 0) {
        sigmaHistory.push(result.sigma)
        if (sigmaHistory.length > SIGMA_HISTORY_MAX) sigmaHistory.shift()
      }
      const regimeState = poolConfig.regimeEnabled ? detectRegime(sigmaHistory) : { regime: 'mid' as const, isCompression: false, isTransition: false, currentSigma: result.sigma }
      return { tickWidth: result.tickWidth, stabilityCount: getStabilityCount(result.tickWidth), sigma: result.sigma, regimeState }
    }
    return null
  }

  function getStabilityCount(currentWidth: number): number {
    let count = 0
    for (let i = volHistory.length - 1; i >= 0; i--) {
      if (volHistory[i] === currentWidth) count++
      else break
    }
    return count
  }

  async function runRebalanceCheck() {
    if (isPaused()) {
      log.info('Bot is paused (PAUSED=true in .env), skipping rebalance check')
      return
    }
    if (running) {
      log.warn('Previous check still running, skipping')
      return
    }
    running = true

    try {
      for (const poolConfig of config.pools) {
        const pool = await getPool(poolConfig.poolId)
        let positions = await getPositions(address, [poolConfig.poolId])

        // Filter to managed positions only (if specified)
        if (poolConfig.positionIds && poolConfig.positionIds.length > 0) {
          const allowed = new Set(poolConfig.positionIds)
          positions = positions.filter(p => allowed.has(p.positionId))
        }

        if (positions.length === 0) {
          log.info('No managed positions found', { poolId: poolConfig.poolId })
          continue
        }

        // Fetch fees periodically (not every check cycle) to avoid excessive RPC calls
        const positionIds = positions.map(p => p.positionId)
        const now = Date.now()
        const shouldFetchFees = (now - lastFeeFetchTime) / 1000 >= FEE_FETCH_INTERVAL_SEC

        if (shouldFetchFees) {
          try {
            const feeMap = await fetchPositionFees(positionIds)
            for (const [posId, fees] of feeMap) {
              feeTracker.record(posId, fees.feeA, fees.feeB)
            }
            lastFeeFetchTime = now
          } catch (err) {
            log.warn('Failed to fetch position fees for tracking', {
              error: err instanceof Error ? err.message : String(err),
            })
          }
        }

        // Fetch optimal tick width from volatility engine (5-min cache)
        const volData = await getOptimalTickWidth(poolConfig.poolId, pool.tickSpacing, poolConfig).catch(() => null)

        // SUI price in USDC (how many USDC per 1 SUI) for USD conversions
        const suiPriceUsdc = coinBPriceInCoinA(pool, DECIMALS_A, DECIMALS_B)

        for (const position of positions) {
          // Circuit breaker: skip positions that have hit max failures
          if (skippedPositions.has(position.positionId)) {
            log.debug('Skipping position (circuit breaker active)', { positionId: position.positionId })
            continue
          }

          // Backoff: skip if still in cooldown period
          const backoffDeadline = backoffUntil.get(position.positionId)
          if (backoffDeadline && Date.now() < backoffDeadline) {
            log.debug('Skipping position (backoff active)', {
              positionId: position.positionId,
              remainingSec: ((backoffDeadline - Date.now()) / 1000).toFixed(0),
            })
            continue
          }

          // Build fee context from observed data
          let observedHourlyFeeUsd: number | undefined
          let positionValueUsd: number | undefined

          const hourlyRate = feeTracker.getHourlyRate(position.positionId)
          if (hourlyRate) {
            observedHourlyFeeUsd = feeToUsd(
              hourlyRate.feeAPerHour,
              hourlyRate.feeBPerHour,
              suiPriceUsdc,
            )
            log.debug('Observed fee rate', {
              positionId: position.positionId,
              hourlyFeeUsd: observedHourlyFeeUsd.toFixed(4),
              observationHours: hourlyRate.observationHours.toFixed(2),
              feeAPerHour: hourlyRate.feeAPerHour.toString(),
              feeBPerHour: hourlyRate.feeBPerHour.toString(),
            })
          }

          // Estimate position value in USD
          const amounts = estimatePositionAmounts(pool, position)
          const valueA = Number(amounts.amountA) / (10 ** DECIMALS_A) // USDC
          const valueB = (Number(amounts.amountB) / (10 ** DECIMALS_B)) * suiPriceUsdc
          positionValueUsd = valueA + valueB

          // Determine when the position was opened (use lastRebalanceTime as proxy,
          // fall back to scheduler start time to protect against post-restart threshold triggers)
          // Use persisted rebalance time; fallback 0 = position is old enough (no guard)
          const positionOpenedAt = getLastRebalanceTime(position.positionId) ?? 0

          const { decision, result, newPositionId } = await checkAndRebalance(
            pool,
            position,
            config,
            poolConfig,
            keypair,
            { observedHourlyFeeUsd, positionValueUsd },
            { positionOpenedAt, optimalTickWidth: volData?.tickWidth, volStabilityCount: volData?.stabilityCount, regimeState: volData?.regimeState },
          )

          // Update managed position ID if rebalance created a new position
          if (newPositionId) {
            if (poolConfig.positionIds) {
              const idx = poolConfig.positionIds.indexOf(position.positionId)
              if (idx !== -1) {
                poolConfig.positionIds[idx] = newPositionId
              } else {
                // Old ID not found (e.g. restored from state.json) — append
                poolConfig.positionIds.push(newPositionId)
              }
            } else {
              // POSITION_IDS was not set in .env — start tracking explicitly
              poolConfig.positionIds = [newPositionId]
            }
            log.info('Managed position updated', {
              old: position.positionId,
              new: newPositionId,
              tracked: poolConfig.positionIds,
            })
            // Persist new position ID to state.json for crash recovery
            savePositionId(poolConfig.poolId, newPositionId)
          }

          if (result && !result.success) {
            const posId = position.positionId
            const count = (failureCounts.get(posId) ?? 0) + 1
            failureCounts.set(posId, count)

            if (count >= MAX_CONSECUTIVE_FAILURES) {
              log.error('Position hit max consecutive failures — circuit breaker activated', {
                positionId: posId,
                failures: count,
                error: result.error,
              })
              skippedPositions.add(posId)
              recordEvent('scheduler_halt', {
                error: result.error,
                reason: 'circuit_breaker',
                consecutiveFailures: count,
              }, posId, poolConfig.poolId)
            } else {
              // Exponential backoff: 60s × count, max 300s
              const backoffSec = Math.min(60 * count, 300)
              backoffUntil.set(posId, Date.now() + backoffSec * 1000)
              log.warn('Rebalance failed — backing off', {
                positionId: posId,
                failures: count,
                backoffSec,
                error: result.error,
              })
              recordEvent('rebalance_error', {
                error: result.error,
                consecutiveFailures: count,
                backoffSec,
              }, posId, poolConfig.poolId)
            }
          } else if (result?.success) {
            // Reset failure counter on success
            failureCounts.delete(position.positionId)
            backoffUntil.delete(position.positionId)
          }
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      log.error('Rebalance check error', { error: msg })
    } finally {
      running = false
    }
  }

  async function runHarvestCheck() {
    if (isPaused()) {
      return
    }
    try {
      for (const poolConfig of config.pools) {
        const pool = await getPool(poolConfig.poolId)
        let positions = await getPositions(address, [poolConfig.poolId])

        if (poolConfig.positionIds && poolConfig.positionIds.length > 0) {
          const allowed = new Set(poolConfig.positionIds)
          positions = positions.filter(p => allowed.has(p.positionId))
        }

        for (const position of positions) {
          await checkAndHarvest(pool, position, config, keypair)
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      log.error('Harvest check error', { error: msg })
    }
  }

  function stop() {
    clearInterval(rebalanceTimer)
    clearTimeout(harvestTimer)
    log.info('Scheduler stopped')
  }

  // Initial run
  log.info('Starting scheduler', {
    checkIntervalSec: config.checkIntervalSec,
    harvestIntervalSec: config.harvestIntervalSec,
    pools: config.pools.map(p => ({
      poolId: p.poolId,
      managedPositions: p.positionIds ?? 'ALL',
    })),
    dryRun: config.dryRun,
  })

  recordEvent('scheduler_start', {
    checkIntervalSec: config.checkIntervalSec,
    harvestIntervalSec: config.harvestIntervalSec,
    pools: config.pools.map(p => p.poolId),
    dryRun: config.dryRun,
  })

  // Run immediately on start
  runRebalanceCheck()

  rebalanceTimer = setInterval(runRebalanceCheck, config.checkIntervalSec * 1000)

  // Harvest runs at fixed wall-clock times: every even hour :00 (0:00, 2:00, …, 22:00 UTC)
  const HARVEST_HOUR_INTERVAL = 2
  function scheduleNextHarvest() {
    const now = new Date()
    const currentHour = now.getUTCHours()
    // Next even hour
    let nextHour = currentHour + (HARVEST_HOUR_INTERVAL - (currentHour % HARVEST_HOUR_INTERVAL))
    const next = new Date(now)
    next.setUTCHours(nextHour, 0, 0, 0)
    if (next.getTime() <= now.getTime()) {
      next.setUTCHours(nextHour + HARVEST_HOUR_INTERVAL, 0, 0, 0)
    }
    const delayMs = next.getTime() - now.getTime()
    log.info('Next harvest scheduled', {
      nextUtc: next.toISOString(),
      delayMin: (delayMs / 60_000).toFixed(1),
    })
    harvestTimer = setTimeout(async () => {
      await runHarvestCheck()
      scheduleNextHarvest()
    }, delayMs)
  }
  scheduleNextHarvest()

  return () => {
    stop()
    recordEvent('scheduler_stop', {})
    closeEventLog()
  }
}
