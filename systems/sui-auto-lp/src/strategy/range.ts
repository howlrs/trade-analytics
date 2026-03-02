import type { RangeStrategy, PoolInfo } from '../types/index.js'
import type { PoolConfig } from '../types/config.js'
import { tickToPrice, getTickFromPrice, getCurrentPrice, alignTickToSpacing } from '../core/price.js'
import { getLogger } from '../utils/logger.js'

interface RangeResult {
  tickLower: number
  tickUpper: number
  priceLower: number
  priceUpper: number
  strategy: RangeStrategy
}

export function calculateOptimalRange(
  pool: PoolInfo,
  poolConfig: PoolConfig,
  decimalsA: number,
  decimalsB: number,
  volatilityTickWidth?: number,
): RangeResult {
  const log = getLogger()
  const currentPrice = getCurrentPrice(pool, decimalsA, decimalsB)
  const strategy = poolConfig.strategy

  // For dynamic strategy with volatility data, use tick-based range directly
  if (strategy === 'dynamic' && volatilityTickWidth != null) {
    const halfWidth = Math.floor(volatilityTickWidth / 2)
    const tickLower = alignTickToSpacing(pool.currentTickIndex - halfWidth, pool.tickSpacing)
    const tickUpper = alignTickToSpacing(pool.currentTickIndex + halfWidth, pool.tickSpacing)

    const actualLower = tickToPrice(tickLower, decimalsA, decimalsB)
    const actualUpper = tickToPrice(tickUpper, decimalsA, decimalsB)

    log.info('Optimal range calculated (volatility-based)', {
      strategy,
      currentPrice,
      volatilityTickWidth,
      tickLower,
      tickUpper,
      priceLower: actualLower,
      priceUpper: actualUpper,
    })

    return {
      tickLower,
      tickUpper,
      priceLower: actualLower,
      priceUpper: actualUpper,
      strategy,
    }
  }

  let rangePct: number
  switch (strategy) {
    case 'narrow':
      rangePct = poolConfig.narrowRangePct
      break
    case 'wide':
      rangePct = poolConfig.wideRangePct
      break
    case 'dynamic':
      rangePct = calculateDynamicRange(poolConfig.narrowRangePct, poolConfig.wideRangePct)
      break
    default:
      rangePct = poolConfig.narrowRangePct
  }

  const priceLower = currentPrice * (1 - rangePct)
  const priceUpper = currentPrice * (1 + rangePct)

  const tickLower = getTickFromPrice(priceLower, decimalsA, decimalsB, pool.tickSpacing)
  const tickUpper = getTickFromPrice(priceUpper, decimalsA, decimalsB, pool.tickSpacing)

  const actualLower = tickToPrice(tickLower, decimalsA, decimalsB)
  const actualUpper = tickToPrice(tickUpper, decimalsA, decimalsB)

  log.info('Optimal range calculated', {
    strategy,
    currentPrice,
    rangePct,
    tickLower,
    tickUpper,
    priceLower: actualLower,
    priceUpper: actualUpper,
  })

  return {
    tickLower,
    tickUpper,
    priceLower: actualLower,
    priceUpper: actualUpper,
    strategy,
  }
}

function calculateDynamicRange(narrowPct: number, widePct: number): number {
  // Start with midpoint between narrow and wide
  // TODO: incorporate on-chain volatility data or historical price range
  return (narrowPct + widePct) / 2
}
