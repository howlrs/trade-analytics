import { initCetusSDK, type CetusClmmSDK } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { AggregatorClient, Env } from '@cetusprotocol/aggregator-sdk'
import type { SuiClient } from '@mysten/sui/client'
import type { PoolInfo } from '../types/index.js'
import { getLogger } from '../utils/logger.js'

let sdk: CetusClmmSDK | null = null
let aggClient: AggregatorClient | null = null

export function initCetusSdk(network: 'mainnet' | 'testnet', wallet?: string): CetusClmmSDK {
  sdk = initCetusSDK({ network })
  if (wallet) {
    sdk.senderAddress = wallet
  }
  getLogger().info('Cetus SDK initialized', { network })
  return sdk
}

export function getCetusSdk(): CetusClmmSDK {
  if (!sdk) throw new Error('Cetus SDK not initialized. Call initCetusSdk first.')
  return sdk
}

export function initAggregatorClient(network: 'mainnet' | 'testnet', client: SuiClient): AggregatorClient {
  aggClient = new AggregatorClient({
    client: client as any,
    env: network === 'mainnet' ? Env.Mainnet : Env.Testnet,
  })
  getLogger().info('AggregatorClient initialized', { network })
  return aggClient
}

export function getAggregatorClient(): AggregatorClient {
  if (!aggClient) throw new Error('AggregatorClient not initialized. Call initAggregatorClient first.')
  return aggClient
}

export async function getPool(poolId: string): Promise<PoolInfo> {
  const log = getLogger()
  const cetusSDK = getCetusSdk()
  const pool = await cetusSDK.Pool.getPool(poolId)

  // Extract active rewarder coin types
  const rewarderCoinTypes: string[] = []
  if (pool.rewarder_infos && Array.isArray(pool.rewarder_infos)) {
    for (const info of pool.rewarder_infos) {
      if (info.coinAddress) {
        rewarderCoinTypes.push(info.coinAddress)
      }
    }
  }

  const info: PoolInfo = {
    poolId: pool.poolAddress,
    coinTypeA: pool.coinTypeA,
    coinTypeB: pool.coinTypeB,
    currentSqrtPrice: BigInt(pool.current_sqrt_price.toString()),
    currentTickIndex: pool.current_tick_index,
    feeRate: pool.fee_rate,
    liquidity: BigInt(pool.liquidity.toString()),
    tickSpacing: Number(pool.tickSpacing),
    rewarderCoinTypes,
  }

  log.debug('Pool fetched', { poolId, tick: info.currentTickIndex })
  return info
}

export async function getPoolsInfo(poolIds: string[]): Promise<PoolInfo[]> {
  const results: PoolInfo[] = []
  for (const id of poolIds) {
    results.push(await getPool(id))
  }
  return results
}
