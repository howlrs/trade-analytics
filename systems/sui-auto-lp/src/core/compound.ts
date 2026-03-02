import type { PoolInfo, PositionInfo, HarvestDecision, TransactionResult } from '../types/index.js'
import type { Config } from '../types/config.js'
import { fetchPositionFees, fetchPositionRewards, sendTx } from './position.js'
import { coinBPriceInCoinA, getCetusUsdPrice, rewardToUsd } from './price.js'
import { getCetusSdk } from './pool.js'
import { getLogger } from '../utils/logger.js'
import { recordEvent } from '../utils/event-log.js'
import { getSuiClient } from '../utils/sui.js'
import type { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'

const DECIMALS_A = 6 // USDC
const DECIMALS_B = 9 // SUI

// Minimum SUI balance required before harvesting (0.05 SUI)
const MIN_SUI_FOR_GAS = 50_000_000n

export async function evaluateHarvest(
  position: PositionInfo,
  pool: PoolInfo,
  config: Config,
): Promise<HarvestDecision> {
  const log = getLogger()

  // 1. Fetch fees
  const fees = await fetchPositionFees([position.positionId])
  const positionFees = fees.get(position.positionId)
  const feeA = positionFees?.feeA ?? 0n
  const feeB = positionFees?.feeB ?? 0n

  // 2. Fetch rewards
  const rewardAmounts = await fetchPositionRewards(pool.poolId, position.positionId)

  // 3. Get prices for USD conversion
  // SUI price in USDC (how many USDC per 1 SUI)
  const suiPriceUsdc = coinBPriceInCoinA(pool, DECIMALS_A, DECIMALS_B)
  const cetusUsdPrice = await getCetusUsdPrice(suiPriceUsdc)

  // 4. Convert fees to USD
  const feeAUsd = Number(feeA) / (10 ** DECIMALS_A)  // USDC = USD
  const feeBUsd = (Number(feeB) / (10 ** DECIMALS_B)) * suiPriceUsdc

  // 5. Convert rewards to USD
  let rewardsUsd = 0
  for (const r of rewardAmounts) {
    rewardsUsd += rewardToUsd(r.coinType, r.amount, suiPriceUsdc, cetusUsdPrice)
  }

  const totalUsd = feeAUsd + feeBUsd + rewardsUsd
  const estimatedGasCost = 5_000_000n

  log.debug('Harvest evaluation', {
    feeA: feeA.toString(),
    feeB: feeB.toString(),
    feeAUsd: feeAUsd.toFixed(4),
    feeBUsd: feeBUsd.toFixed(4),
    rewardsUsd: rewardsUsd.toFixed(4),
    totalUsd: totalUsd.toFixed(4),
    threshold: config.harvestThresholdUsd,
    rewardCount: rewardAmounts.length,
    currentTick: pool.currentTickIndex,
    tickLower: position.tickLowerIndex,
    tickUpper: position.tickUpperIndex,
  })

  if (feeA === 0n && feeB === 0n && rewardAmounts.length === 0) {
    return {
      shouldHarvest: false,
      feeValueA: feeA,
      feeValueB: feeB,
      rewardAmounts,
      totalUsd,
      estimatedGasCost,
      reason: 'No fees or rewards to harvest',
    }
  }

  if (totalUsd < config.harvestThresholdUsd) {
    return {
      shouldHarvest: false,
      feeValueA: feeA,
      feeValueB: feeB,
      rewardAmounts,
      totalUsd,
      estimatedGasCost,
      reason: `Total USD $${totalUsd.toFixed(4)} below threshold $${config.harvestThresholdUsd}`,
    }
  }

  return {
    shouldHarvest: true,
    feeValueA: feeA,
    feeValueB: feeB,
    rewardAmounts,
    totalUsd,
    estimatedGasCost,
    reason: `Claim: fees A=${feeA}, B=${feeB} + rewards $${rewardsUsd.toFixed(4)}, total $${totalUsd.toFixed(2)}`,
  }
}

export async function checkAndHarvest(
  pool: PoolInfo,
  position: PositionInfo,
  config: Config,
  keypair: Ed25519Keypair,
): Promise<TransactionResult | null> {
  const log = getLogger()

  const decision = await evaluateHarvest(position, pool, config)
  log.info('Harvest evaluation', {
    positionId: position.positionId,
    shouldHarvest: decision.shouldHarvest,
    totalUsd: decision.totalUsd.toFixed(4),
    reason: decision.reason,
  })

  recordEvent(
    decision.shouldHarvest ? 'harvest_check' : 'harvest_skip',
    {
      shouldHarvest: decision.shouldHarvest,
      feeA: decision.feeValueA.toString(),
      feeB: decision.feeValueB.toString(),
      rewardCount: decision.rewardAmounts.length,
      totalUsd: decision.totalUsd.toFixed(4),
      reason: decision.reason,
    },
    position.positionId,
    pool.poolId,
  )

  if (!decision.shouldHarvest) {
    return null
  }

  // Pre-flight: ensure wallet has enough SUI for gas
  const owner = keypair.getPublicKey().toSuiAddress()
  const client = getSuiClient()
  const suiBal = await client.getBalance({
    owner,
    coinType: '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI',
  })
  if (BigInt(suiBal.totalBalance) < MIN_SUI_FOR_GAS) {
    const msg = `Insufficient SUI for gas: ${(Number(suiBal.totalBalance) / 1e9).toFixed(4)} SUI`
    log.error(msg)
    recordEvent('harvest_error', { error: msg }, position.positionId, pool.poolId)
    return { success: false, digest: null, gasCost: 0n, error: msg }
  }

  // Claim fees + rewards via collectRewarder
  log.info('Executing harvest (collectRewarder)', {
    positionId: position.positionId,
    feeA: decision.feeValueA.toString(),
    feeB: decision.feeValueB.toString(),
    rewarderCoinTypes: pool.rewarderCoinTypes,
  })

  let result: TransactionResult
  try {
    const sdk = getCetusSdk()
    const payload = await sdk.Rewarder.collectRewarderTransactionPayload({
      pool_id: pool.poolId,
      pos_id: position.positionId,
      coinTypeA: pool.coinTypeA,
      coinTypeB: pool.coinTypeB,
      collect_fee: true,
      rewarder_coin_types: pool.rewarderCoinTypes,
    })
    result = await sendTx(payload, keypair, config.dryRun)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    log.error('Failed to build harvest TX', { error: msg })
    recordEvent('harvest_error', { error: msg }, position.positionId, pool.poolId)
    return { success: false, digest: null, gasCost: 0n, error: msg }
  }

  if (result.success) {
    recordEvent('harvest_execute', {
      digest: result.digest,
      gasCost: result.gasCost.toString(),
      feeA: decision.feeValueA.toString(),
      feeB: decision.feeValueB.toString(),
      rewardCount: decision.rewardAmounts.length,
      totalUsd: decision.totalUsd.toFixed(4),
    }, position.positionId, pool.poolId)
  } else {
    recordEvent('harvest_error', {
      error: result.error,
    }, position.positionId, pool.poolId)
  }

  log.info('Harvest result', {
    positionId: position.positionId,
    success: result.success,
    digest: result.digest,
    error: result.error,
  })

  return result
}
