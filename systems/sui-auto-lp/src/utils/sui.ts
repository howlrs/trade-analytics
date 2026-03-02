import { SuiClient, getFullnodeUrl } from '@mysten/sui/client'
import { getLogger } from './logger.js'

const NETWORK_URLS: Record<string, string> = {
  mainnet: getFullnodeUrl('mainnet'),
  testnet: getFullnodeUrl('testnet'),
}

let client: SuiClient | null = null

export function initSuiClient(network: string): SuiClient {
  const url = NETWORK_URLS[network]
  if (!url) throw new Error(`Unknown network: ${network}`)
  client = new SuiClient({ url })
  getLogger().info('SuiClient initialized', { network, url })
  return client
}

export function getSuiClient(): SuiClient {
  if (!client) throw new Error('SuiClient not initialized. Call initSuiClient first.')
  return client
}

/** Clamp gas cost to zero when storageRebate exceeds computation + storage. */
export function clampGasCost(gasUsed: { computationCost: string; storageCost: string; storageRebate: string }): bigint {
  const cost = BigInt(gasUsed.computationCost) + BigInt(gasUsed.storageCost) - BigInt(gasUsed.storageRebate)
  return cost < 0n ? 0n : cost
}
