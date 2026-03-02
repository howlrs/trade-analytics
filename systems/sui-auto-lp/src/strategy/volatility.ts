import { TickMath } from '@cetusprotocol/cetus-sui-clmm-sdk'
import BN from 'bn.js'
import { getSuiClient } from '../utils/sui.js'
import { alignTickToSpacing } from '../core/price.js'
import { getLogger } from '../utils/logger.js'

// Cetus CLMM package on Sui mainnet
const CETUS_CLMM_PACKAGE = '0x1eabed72c53feb3805120a081dc15963c204dc8d091542592abaf7a35689b2fb'
const SWAP_EVENT_TYPE = `${CETUS_CLMM_PACKAGE}::pool::SwapEvent`

const DEFAULT_LOOKBACK_HOURS = 2
const EXTENDED_LOOKBACK_MULTIPLIERS = [2, 3] // 2x and 3x the original lookback
const CACHE_MAX_AGE_MS = 4 * 60 * 60 * 1000 // 4 hours

// Maximum pages to fetch (50 events/page × 20 pages = 1000 events max)
const MAX_PAGES = 20

// Module-level cache for the last successful volatility result per pool
const lastValidResult = new Map<string, { tickWidth: number; sigma: number; timestamp: number }>()

// sigma-to-tickWidth discrete mapping (per hour sigma)
// Wider tiers to reduce rebalance frequency (Issue #29)
const SIGMA_TIERS: { maxSigma: number; tickWidth: number }[] = [
  { maxSigma: 40, tickWidth: 480 },
  { maxSigma: 80, tickWidth: 720 },
  { maxSigma: 120, tickWidth: 960 },
]
const MAX_TICK_WIDTH = 1200

export interface VolatilityResult {
  tickWidth: number
  sigma: number
}

/**
 * Continuous linear interpolation from sigma to tick width.
 * Replaces the discrete tier table when volScalingMode === 'continuous'.
 *
 *   t = clamp((sigma - sigmaLow) / (sigmaHigh - sigmaLow), 0, 1)
 *   tickWidth = round(minWidth + (maxWidth - minWidth) * t)
 */
export function sigmaToTickWidth(
  sigma: number,
  sigmaLow: number,
  sigmaHigh: number,
  minWidth: number,
  maxWidth: number,
): number {
  const t = Math.max(0, Math.min(1, (sigma - sigmaLow) / (sigmaHigh - sigmaLow)))
  return Math.round(minWidth + (maxWidth - minWidth) * t)
}

/**
 * Fetch 24h hourly klines from Binance REST API and compute
 * log-return standard deviation in tick units.
 * Returns sigma (per hour, in tick units) or null on failure.
 */
export async function fetchBinanceSigma(): Promise<number | null> {
  const log = getLogger()
  try {
    const url = 'https://api.binance.com/api/v3/klines?symbol=SUIUSDT&interval=1h&limit=24'
    const res = await fetch(url)
    if (!res.ok) {
      log.warn('Binance kline fetch failed', { status: res.status })
      return null
    }
    const klines = await res.json() as Array<[number, string, string, string, string, ...unknown[]]>
    if (klines.length < 2) return null

    // Compute log returns from close prices
    const closes = klines.map(k => parseFloat(k[4]))
    const logReturns: number[] = []
    for (let i = 1; i < closes.length; i++) {
      if (closes[i - 1] > 0) {
        logReturns.push(Math.log(closes[i] / closes[i - 1]))
      }
    }
    if (logReturns.length < 2) return null

    const mean = logReturns.reduce((a, b) => a + b, 0) / logReturns.length
    const variance = logReturns.reduce((sum, v) => sum + (v - mean) ** 2, 0) / logReturns.length
    const std = Math.sqrt(variance)

    // Convert from price-return std to tick units (1 tick ≈ 1 bps = 0.0001)
    const sigmaInTicks = std / 0.0001

    log.info('Binance sigma computed', {
      klines: klines.length,
      sigmaInTicks: sigmaInTicks.toFixed(2),
      priceStd: std.toFixed(6),
    })

    return sigmaInTicks
  } catch (err) {
    log.warn('Binance sigma fetch error', {
      error: err instanceof Error ? err.message : String(err),
    })
    return null
  }
}

/**
 * Convert sqrtPriceX64 to approximate tick index.
 * Uses the Cetus SDK TickMath for accuracy.
 */
function sqrtPriceToTick(sqrtPriceStr: string): number {
  try {
    return TickMath.sqrtPriceX64ToTickIndex(new BN(sqrtPriceStr))
  } catch {
    // Fallback: manual approximation
    // tick = 2 * log(sqrtPriceX64 / 2^64) / log(1.0001)
    const sqrtP = Number(BigInt(sqrtPriceStr)) / (2 ** 64)
    if (sqrtP <= 0) return 0
    return Math.round(2 * Math.log(sqrtP) / Math.log(1.0001))
  }
}

/**
 * Fetch recent SwapEvents for a pool with a specific lookback window,
 * then compute tick-change volatility (σ).
 * Returns result or null if insufficient data.
 */
async function fetchAndComputeVolatility(
  poolId: string,
  tickSpacing: number,
  hours: number,
  tickWidthMin?: number,
  tickWidthMax?: number,
  scalingMode?: 'tiered' | 'continuous',
  sigmaLow?: number,
  sigmaHigh?: number,
): Promise<VolatilityResult | null> {
  const log = getLogger()
  const cutoffMs = Date.now() - hours * 60 * 60 * 1000
  const client = getSuiClient()

  const poolEvents: Array<{ tick: number; timestampMs: number }> = []
  let cursor: string | null | undefined = undefined
  let hasNextPage = true
  let totalFetched = 0

  for (let page = 0; page < MAX_PAGES && hasNextPage; page++) {
    const result = await client.queryEvents({
      query: { MoveEventType: SWAP_EVENT_TYPE },
      order: 'descending',
      limit: 50,
      ...(cursor ? { cursor: { txDigest: cursor as string, eventSeq: '0' } } : {}),
    })

    if (!result.data || result.data.length === 0) break
    totalFetched += result.data.length

    for (const e of result.data) {
      const parsed = e.parsedJson as Record<string, unknown> | undefined
      if (!parsed) continue

      const eventPool = parsed.pool as string | undefined
      if (eventPool !== poolId) continue

      const ts = Number(e.timestampMs ?? 0)
      if (ts < cutoffMs) {
        hasNextPage = false
        break
      }

      const afterSqrtPrice = parsed.after_sqrt_price as string | undefined
      if (!afterSqrtPrice) continue

      const tick = sqrtPriceToTick(afterSqrtPrice)
      poolEvents.push({ tick, timestampMs: ts })
    }

    hasNextPage = result.hasNextPage && hasNextPage
    cursor = result.nextCursor?.txDigest ?? null
    if (!cursor) break
    if (poolEvents.length >= 30) break
  }

  if (poolEvents.length < 2) {
    log.info('Volatility: insufficient swap events for pool', {
      poolId,
      eventCount: poolEvents.length,
      totalFetched,
      pagesScanned: Math.ceil(totalFetched / 50),
      lookbackHours: hours,
    })
    return null
  }

  const ticks = poolEvents.map(e => e.tick)

  const tickChanges: number[] = []
  for (let i = 0; i < ticks.length - 1; i++) {
    tickChanges.push(Math.abs(ticks[i] - ticks[i + 1]))
  }

  const mean = tickChanges.reduce((a, b) => a + b, 0) / tickChanges.length
  const variance =
    tickChanges.reduce((sum, v) => sum + (v - mean) ** 2, 0) /
    tickChanges.length
  const sigma = Math.sqrt(variance)

  const oldestTs = poolEvents[poolEvents.length - 1].timestampMs
  const newestTs = poolEvents[0].timestampMs
  const spanHours = Math.max((newestTs - oldestTs) / (60 * 60 * 1000), 0.1)
  const sigmaPerHour = sigma * Math.sqrt(ticks.length / spanHours)

  const effectiveMin = tickWidthMin ?? SIGMA_TIERS[0].tickWidth
  const effectiveMax = tickWidthMax ?? MAX_TICK_WIDTH
  let tickWidth: number
  if (scalingMode === 'continuous') {
    const effSigmaLow = sigmaLow ?? 40
    const effSigmaHigh = sigmaHigh ?? 120
    tickWidth = sigmaToTickWidth(sigmaPerHour, effSigmaLow, effSigmaHigh, effectiveMin, effectiveMax)
  } else {
    // Tiered (legacy) mode
    tickWidth = effectiveMax
    for (const tier of SIGMA_TIERS) {
      if (sigmaPerHour < tier.maxSigma) {
        tickWidth = tier.tickWidth
        break
      }
    }
  }
  tickWidth = Math.max(effectiveMin, Math.min(effectiveMax, tickWidth))

  const alignedWidth =
    Math.max(Math.floor(tickWidth / tickSpacing), 1) * tickSpacing

  log.info('Volatility engine result', {
    poolId,
    eventCount: poolEvents.length,
    tickSamples: ticks.length,
    sigma: sigma.toFixed(2),
    sigmaPerHour: sigmaPerHour.toFixed(2),
    rawTickWidth: tickWidth,
    alignedTickWidth: alignedWidth,
    spanHours: spanHours.toFixed(1),
    totalFetched,
    lookbackHours: hours,
  })

  return { tickWidth: alignedWidth, sigma: sigmaPerHour }
}

/**
 * Fetch recent SwapEvents for a pool with pagination, then compute
 * tick-change volatility (σ).
 * Returns a tick width aligned to tickSpacing.
 *
 * Fallback chain when data is insufficient:
 *   1. Retry with extended lookback (2x, 3x the configured window)
 *   2. Use cached result if less than 4 hours old
 *   3. Return volTickWidthMin as conservative fallback
 */
export async function calculateVolatilityBasedTicks(
  poolId: string,
  tickSpacing: number,
  lookbackHours?: number,
  tickWidthMin?: number,
  tickWidthMax?: number,
  scalingMode?: 'tiered' | 'continuous',
  sigmaLow?: number,
  sigmaHigh?: number,
  binanceVolFallback?: boolean,
): Promise<VolatilityResult | null> {
  const log = getLogger()
  const baseHours = lookbackHours ?? DEFAULT_LOOKBACK_HOURS
  const effectiveMin = tickWidthMin ?? SIGMA_TIERS[0].tickWidth

  try {
    // Try with the configured lookback first
    let result = await fetchAndComputeVolatility(
      poolId, tickSpacing, baseHours, tickWidthMin, tickWidthMax,
      scalingMode, sigmaLow, sigmaHigh,
    )

    // If insufficient data, retry with extended lookback windows
    if (!result) {
      for (const multiplier of EXTENDED_LOOKBACK_MULTIPLIERS) {
        const extendedHours = baseHours * multiplier
        log.info('Volatility: retrying with extended lookback', {
          poolId,
          extendedHours,
        })
        result = await fetchAndComputeVolatility(
          poolId, tickSpacing, extendedHours, tickWidthMin, tickWidthMax,
          scalingMode, sigmaLow, sigmaHigh,
        )
        if (result) break
      }
    }

    // Binance fallback blending (opt-in)
    if (result && binanceVolFallback) {
      const binanceSigma = await fetchBinanceSigma()
      if (binanceSigma != null) {
        // Blend: on-chain 70% + Binance 30%
        const blendedSigma = result.sigma * 0.7 + binanceSigma * 0.3
        const effMax = tickWidthMax ?? MAX_TICK_WIDTH
        const effMin = tickWidthMin ?? SIGMA_TIERS[0].tickWidth
        let blendedWidth: number
        if (scalingMode === 'continuous') {
          blendedWidth = sigmaToTickWidth(
            blendedSigma, sigmaLow ?? 40, sigmaHigh ?? 120, effMin, effMax,
          )
        } else {
          blendedWidth = effMax
          for (const tier of SIGMA_TIERS) {
            if (blendedSigma < tier.maxSigma) {
              blendedWidth = tier.tickWidth
              break
            }
          }
        }
        blendedWidth = Math.max(effMin, Math.min(effMax, blendedWidth))
        const alignedBlended = Math.max(Math.floor(blendedWidth / tickSpacing), 1) * tickSpacing
        log.info('Volatility: Binance blending applied', {
          onChainSigma: result.sigma.toFixed(2),
          binanceSigma: binanceSigma.toFixed(2),
          blendedSigma: blendedSigma.toFixed(2),
          originalWidth: result.tickWidth,
          blendedWidth: alignedBlended,
        })
        result = { tickWidth: alignedBlended, sigma: blendedSigma }
      }
    }

    // Success — cache and return
    if (result) {
      lastValidResult.set(poolId, {
        tickWidth: result.tickWidth,
        sigma: result.sigma,
        timestamp: Date.now(),
      })
      return result
    }

    // All lookback windows failed — try cache
    const cached = lastValidResult.get(poolId)
    if (cached && Date.now() - cached.timestamp < CACHE_MAX_AGE_MS) {
      log.info('Volatility: using cached result (insufficient live data)', {
        poolId,
        cachedTickWidth: cached.tickWidth,
        cachedSigma: cached.sigma.toFixed(2),
        cacheAgeMin: ((Date.now() - cached.timestamp) / 60_000).toFixed(1),
      })
      return { tickWidth: cached.tickWidth, sigma: cached.sigma }
    }

    // No cache available — return conservative fallback (volTickWidthMin)
    const fallbackWidth =
      Math.max(Math.floor(effectiveMin / tickSpacing), 1) * tickSpacing
    log.info('Volatility: no data or cache, using volTickWidthMin fallback', {
      poolId,
      fallbackTickWidth: fallbackWidth,
    })
    return { tickWidth: fallbackWidth, sigma: 0 }
  } catch (err) {
    log.warn('Volatility calculation failed', {
      poolId,
      error: err instanceof Error ? err.message : String(err),
    })

    // On exception, try cache before giving up
    const cached = lastValidResult.get(poolId)
    if (cached && Date.now() - cached.timestamp < CACHE_MAX_AGE_MS) {
      log.info('Volatility: using cached result after error', {
        poolId,
        cachedTickWidth: cached.tickWidth,
        cachedSigma: cached.sigma.toFixed(2),
        cacheAgeMin: ((Date.now() - cached.timestamp) / 60_000).toFixed(1),
      })
      return { tickWidth: cached.tickWidth, sigma: cached.sigma }
    }

    // No cache — return conservative fallback
    const fallbackWidth =
      Math.max(Math.floor(effectiveMin / tickSpacing), 1) * tickSpacing
    log.warn('Volatility: no cache after error, using volTickWidthMin fallback', {
      poolId,
      fallbackTickWidth: fallbackWidth,
    })
    return { tickWidth: fallbackWidth, sigma: 0 }
  }
}
