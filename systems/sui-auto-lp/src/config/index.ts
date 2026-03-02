import dotenv from 'dotenv'
import { ConfigSchema, type Config } from '../types/config.js'
import { loadState } from '../utils/state.js'
import { getLogger } from '../utils/logger.js'

// Load .env with override so the file on disk is always the source of truth.
// (Eliminates conflicts with systemd EnvironmentFile or inherited env vars.)
dotenv.config({ override: true })

export function loadConfig(): Config {
  const poolIds = (process.env.POOL_IDS ?? '')
    .split(',')
    .map(id => id.trim())
    .filter(Boolean)

  const positionIds = (process.env.POSITION_IDS ?? '')
    .split(',')
    .map(id => id.trim())
    .filter(Boolean)

  // Parse per-pool regime/vol config from environment
  const volScalingMode = process.env.VOL_SCALING_MODE as 'tiered' | 'continuous' | undefined
  const sigmaLowEnv = process.env.SIGMA_LOW ? parseFloat(process.env.SIGMA_LOW) : undefined
  const sigmaHighEnv = process.env.SIGMA_HIGH ? parseFloat(process.env.SIGMA_HIGH) : undefined
  const regimeEnabledEnv = process.env.REGIME_ENABLED !== undefined
    ? process.env.REGIME_ENABLED !== 'false'
    : undefined
  const binanceVolFallbackEnv = process.env.BINANCE_VOL_FALLBACK === 'true' ? true : undefined

  const raw = {
    network: process.env.SUI_NETWORK ?? 'testnet',
    privateKey: process.env.SUI_PRIVATE_KEY ?? '',
    pools: poolIds.map(poolId => ({
      poolId,
      ...(positionIds.length > 0 ? { positionIds } : {}),
      ...(volScalingMode ? { volScalingMode } : {}),
      ...(sigmaLowEnv != null ? { sigmaLow: sigmaLowEnv } : {}),
      ...(sigmaHighEnv != null ? { sigmaHigh: sigmaHighEnv } : {}),
      ...(regimeEnabledEnv != null ? { regimeEnabled: regimeEnabledEnv } : {}),
      ...(binanceVolFallbackEnv != null ? { binanceVolFallback: binanceVolFallbackEnv } : {}),
    })),
    rebalanceThreshold: parseFloat(process.env.REBALANCE_THRESHOLD ?? '0.03'),
    harvestIntervalSec: parseInt(process.env.HARVEST_INTERVAL ?? process.env.COMPOUND_INTERVAL ?? '7200', 10),
    checkIntervalSec: parseInt(process.env.CHECK_INTERVAL ?? '30', 10),
    slippageTolerance: parseFloat(process.env.SLIPPAGE_TOLERANCE ?? '0.01'),
    minGasProfitRatio: parseFloat(process.env.MIN_GAS_PROFIT_RATIO ?? '2'),
    logLevel: process.env.LOG_LEVEL ?? 'info',
    dryRun: process.env.DRY_RUN !== 'false',
    harvestThresholdUsd: parseFloat(process.env.HARVEST_THRESHOLD_USD ?? '0.50'),
    maxSwapCostPct: parseFloat(process.env.MAX_SWAP_COST_PCT ?? '0.01'),
    swapFreeRebalance: process.env.SWAP_FREE_REBALANCE !== 'false',
    swapFreeMaxRatioSwap: parseFloat(process.env.SWAP_FREE_MAX_RATIO_SWAP ?? '0'),
    maxIdleSwapRatio: parseFloat(process.env.MAX_IDLE_SWAP_RATIO ?? '0.45'),
    ...(process.env.FALLBACK_DAILY_VOLUME_RATIO ? { fallbackDailyVolumeRatio: parseFloat(process.env.FALLBACK_DAILY_VOLUME_RATIO) } : {}),
    ...(process.env.MAX_BREAKEVEN_HOURS ? { maxBreakevenHours: parseInt(process.env.MAX_BREAKEVEN_HOURS, 10) } : {}),
    ...(process.env.REBALANCE_FREE_HARVEST_USD ? { rebalanceFreeHarvestUsd: parseFloat(process.env.REBALANCE_FREE_HARVEST_USD) } : {}),
  }

  const config = ConfigSchema.parse(raw)

  // Restore position IDs from state.json (overrides .env POSITION_IDS)
  const state = loadState()
  if (state) {
    const log = getLogger()
    for (const pool of config.pools) {
      const savedPositionId = state.positions[pool.poolId]
      if (savedPositionId) {
        const previous = pool.positionIds ? [...pool.positionIds] : []
        pool.positionIds = [savedPositionId]
        log.info('Restored position ID from state.json', {
          poolId: pool.poolId,
          positionId: savedPositionId,
          previousFromEnv: previous,
        })
      }
    }
  }

  return config
}
