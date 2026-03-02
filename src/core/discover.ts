import type { Config } from '../types/config.js'
import { getPositions } from './position.js'
import { savePositionId } from '../utils/state.js'
import { getLogger } from '../utils/logger.js'

/**
 * Discover and validate positions on-chain at startup.
 * - If configured position IDs exist on-chain, keep them.
 * - If configured IDs are stale (not found on-chain), auto-discover
 *   the highest-liquidity position for that pool.
 * - Updates poolConfig.positionIds in-memory and persists to state.json.
 */
export async function discoverPositions(config: Config, ownerAddress: string): Promise<void> {
  const log = getLogger()
  log.info('Position auto-discovery starting', { pools: config.pools.length })

  for (const pool of config.pools) {
    const { poolId } = pool
    const configuredIds = pool.positionIds ?? []

    // Fetch all positions for this pool from on-chain
    let onChainPositions
    try {
      onChainPositions = await getPositions(ownerAddress, [poolId])
    } catch (err) {
      log.error('Failed to fetch positions for pool, skipping discovery', {
        poolId,
        error: err instanceof Error ? err.message : String(err),
      })
      continue
    }

    const onChainIdSet = new Set(onChainPositions.map(p => p.positionId))

    // Check if configured IDs exist on-chain and have liquidity
    if (configuredIds.length > 0) {
      const allExist = configuredIds.every(id => onChainIdSet.has(id))
      const liquidPositionIds = new Set(
        onChainPositions.filter(p => p.liquidity > 0n).map(p => p.positionId),
      )
      const allActive = configuredIds.every(id => liquidPositionIds.has(id))

      if (allExist && allActive) {
        log.info('Configured position IDs verified on-chain', {
          poolId,
          positionIds: configuredIds,
        })
        continue
      }

      if (allExist && !allActive) {
        const emptyIds = configuredIds.filter(id => !liquidPositionIds.has(id))
        log.warn('Configured positions have zero liquidity, running auto-discovery', {
          poolId,
          emptyIds,
        })
      } else {
        const staleIds = configuredIds.filter(id => !onChainIdSet.has(id))
        log.warn('Configured position IDs not found on-chain, running auto-discovery', {
          poolId,
          staleIds,
        })
      }
    }

    // Auto-discovery: find highest-liquidity position
    const activePositions = onChainPositions.filter(p => p.liquidity > 0n)

    if (activePositions.length === 0) {
      // Clear stale IDs so scheduler doesn't attempt to use them (W1)
      if (configuredIds.length > 0) {
        pool.positionIds = []
      }
      if (onChainPositions.length === 0) {
        log.warn('No positions found for pool (pre-LP state)', { poolId })
      } else {
        log.warn('All positions have zero liquidity', {
          poolId,
          total: onChainPositions.length,
        })
      }
      continue
    }

    // Select the position with the highest liquidity
    const best = activePositions.reduce((a, b) => (a.liquidity > b.liquidity ? a : b))

    pool.positionIds = [best.positionId]
    savePositionId(poolId, best.positionId)

    log.info('Auto-discovered position', {
      poolId,
      positionId: best.positionId,
      liquidity: best.liquidity.toString(),
      candidates: activePositions.length,
    })
  }

  log.info('Position auto-discovery complete', {
    positions: config.pools.map(p => ({ poolId: p.poolId, positionIds: p.positionIds ?? [] })),
  })
}
