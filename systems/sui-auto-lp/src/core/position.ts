import { ClmmPoolUtil, TickMath } from '@cetusprotocol/cetus-sui-clmm-sdk'
import BN from 'bn.js'
import type { PositionInfo, RewardAmount, TransactionResult } from '../types/index.js'
import { getCetusSdk } from './pool.js'
import { tickToSqrtPriceX64 } from './price.js'
import { clampGasCost } from '../utils/sui.js'
import { getLogger } from '../utils/logger.js'
import type { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'

export async function getPositions(owner: string, poolIds?: string[]): Promise<PositionInfo[]> {
  const log = getLogger()
  const sdk = getCetusSdk()
  const positions = await sdk.Position.getPositionList(owner, poolIds)

  const result: PositionInfo[] = positions.map(p => ({
    positionId: p.pos_object_id,
    poolId: p.pool,
    owner,
    tickLowerIndex: p.tick_lower_index,
    tickUpperIndex: p.tick_upper_index,
    liquidity: BigInt(p.liquidity.toString()),
    feeOwedA: 0n,
    feeOwedB: 0n,
    rewardAmountOwed: [],
  }))

  log.debug('Positions fetched', { owner, count: result.length })
  return result
}

export async function fetchPositionFees(
  positionIds: string[],
): Promise<Map<string, { feeA: bigint; feeB: bigint }>> {
  const sdk = getCetusSdk()
  const fees = await sdk.Position.batchFetchPositionFees(positionIds)
  const result = new Map<string, { feeA: bigint; feeB: bigint }>()

  for (const [id, fee] of Object.entries(fees)) {
    result.set(id, {
      feeA: BigInt(fee.feeOwedA?.toString() ?? '0'),
      feeB: BigInt(fee.feeOwedB?.toString() ?? '0'),
    })
  }
  return result
}

export async function fetchPositionRewards(
  poolId: string,
  positionId: string,
): Promise<RewardAmount[]> {
  const log = getLogger()
  const sdk = getCetusSdk()

  try {
    const pool = await sdk.Pool.getPool(poolId)
    const rewards = await sdk.Rewarder.fetchPositionRewarders(pool, positionId)

    const result: RewardAmount[] = []
    if (rewards && Array.isArray(rewards)) {
      for (const r of rewards) {
        const amount = BigInt(r.amount_owed?.toString() ?? '0')
        if (amount > 0n && r.coin_address) {
          result.push({ coinType: r.coin_address, amount })
        }
      }
    }

    log.debug('Position rewards fetched', {
      positionId,
      rewardCount: result.length,
      rewards: result.map(r => ({ coinType: r.coinType, amount: r.amount.toString() })),
    })
    return result
  } catch (err) {
    log.warn('Failed to fetch position rewards', {
      positionId,
      error: err instanceof Error ? err.message : String(err),
    })
    return []
  }
}

export async function sendTx(
  payload: any,
  keypair: Ed25519Keypair,
  dryRunOnly: boolean,
): Promise<TransactionResult> {
  const log = getLogger()
  const sdk = getCetusSdk()

  // Dry-run via devInspect
  try {
    const sender = keypair.getPublicKey().toSuiAddress()
    payload.setSender(sender)
    const txBytes = await payload.build({ client: sdk.fullClient })
    const dryResult = await sdk.fullClient.dryRunTransactionBlock({
      transactionBlock: Buffer.from(txBytes).toString('base64'),
    })

    const status = dryResult.effects.status.status
    const gasCost = clampGasCost(dryResult.effects.gasUsed)

    if (status !== 'success') {
      const error = dryResult.effects.status.error ?? 'Dry-run failed'
      log.warn('Dry-run failed', { error })
      return { success: false, digest: null, gasCost, error }
    }

    log.debug('Dry-run succeeded', { gasCost: gasCost.toString() })

    if (dryRunOnly) {
      return { success: true, digest: null, gasCost, error: null }
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    log.error('Dry-run exception', { error: msg })
    return { success: false, digest: null, gasCost: 0n, error: msg }
  }

  // Execute
  const response = await sdk.fullClient.sendTransaction(keypair as any, payload)
  if (!response) {
    return { success: false, digest: null, gasCost: 0n, error: 'sendTransaction returned undefined' }
  }

  const effects = response.effects!
  const gasCost = clampGasCost(effects.gasUsed)

  log.info('Transaction executed', { digest: response.digest })
  return { success: true, digest: response.digest, gasCost, error: null }
}

/**
 * Determine which token is the bottleneck (scarce) for the given tick range.
 * Fixing the bottleneck token first avoids a wasted RPC call.
 * Returns true if coinA should be fixed first.
 */
function shouldFixAmountA(
  tickLower: number,
  tickUpper: number,
  currentSqrtPrice: bigint,
  amountA: string,
  amountB: string,
  decimalsA: number,
  decimalsB: number,
): boolean {
  try {
    const { ratioA, ratioB } = ClmmPoolUtil.calculateDepositRatioFixTokenA(
      tickLower,
      tickUpper,
      new BN(currentSqrtPrice.toString()),
    )
    const targetRatioA = ratioA.toNumber() / (ratioA.toNumber() + ratioB.toNumber())

    // Convert to USD-equivalent values using the pool's price
    // sqrtPriceX64 → price(coinB/coinA), so priceB_in_A = 1/price
    const priceRaw = TickMath.sqrtPriceX64ToPrice(
      new BN(currentSqrtPrice.toString()), decimalsA, decimalsB
    ).toNumber()
    // priceRaw = coinB per coinA (e.g. SUI per USDC)
    // For USD comparison: 1 coinA = priceRaw coinB, so coinB_usd = coinA_usd / priceRaw
    const valA = Number(amountA) / (10 ** decimalsA)
    const valB = (Number(amountB) / (10 ** decimalsB)) / priceRaw // convert coinB to coinA-equivalent

    const total = valA + valB
    if (total === 0) return true
    const actualRatioA = valA / total
    // Fix the scarce (bottleneck) token: if A is relatively scarce, fix A
    return actualRatioA < targetRatioA
  } catch {
    return true // default: try fix_amount_a=true first
  }
}

export async function openPosition(
  poolId: string,
  coinTypeA: string,
  coinTypeB: string,
  tickLower: number,
  tickUpper: number,
  amountA: string,
  amountB: string,
  slippage: number,
  keypair: Ed25519Keypair,
  dryRunOnly: boolean,
  rewarderCoinTypes: string[] = [],
  currentSqrtPrice?: bigint,
  decimalsA = 6,
  decimalsB = 9,
): Promise<TransactionResult> {
  const log = getLogger()
  const sdk = getCetusSdk()

  try {
    const preferFixA = currentSqrtPrice != null
      ? shouldFixAmountA(tickLower, tickUpper, currentSqrtPrice, amountA, amountB, decimalsA, decimalsB)
      : true
    // Try bottleneck token first; if it fails, retry with the other direction
    for (const fixA of [preferFixA, !preferFixA]) {
      log.info('Attempting openPosition', { fixA, amountA, amountB })

      const payload = await sdk.Position.createAddLiquidityFixTokenPayload({
        pool_id: poolId,
        coinTypeA,
        coinTypeB,
        tick_lower: tickLower,
        tick_upper: tickUpper,
        is_open: true,
        pos_id: '',
        fix_amount_a: fixA,
        amount_a: amountA,
        amount_b: amountB,
        slippage,
        collect_fee: false,
        rewarder_coin_types: rewarderCoinTypes,
      })

      const result = await sendTx(payload, keypair, dryRunOnly)
      if (result.success) {
        log.info('Position opened', { poolId, tickLower, tickUpper, fixA, digest: result.digest })
        return result
      }

      // If first attempt failed, try the other fix direction
      if (fixA === preferFixA) {
        log.warn(`openPosition with fix_amount_a=${fixA} failed, retrying with ${!fixA}`, {
          error: result.error,
        })
        continue
      }

      // Both failed
      return result
    }

    // Should not reach here
    return { success: false, digest: null, gasCost: 0n, error: 'Unexpected state' }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    log.error('Failed to open position', { poolId, error: msg })
    return { success: false, digest: null, gasCost: 0n, error: msg }
  }
}

/**
 * Remove all liquidity from a position and collect fees.
 * Does NOT burn the position NFT — the empty NFT remains (harmless).
 * This is used by the rebalance flow before opening a new position.
 */
export async function closePosition(
  poolId: string,
  positionId: string,
  coinTypeA: string,
  coinTypeB: string,
  liquidity: bigint,
  currentSqrtPrice: bigint,
  tickLower: number,
  tickUpper: number,
  slippage: number,
  keypair: Ed25519Keypair,
  dryRunOnly: boolean,
  rewarderCoinTypes: string[] = [],
): Promise<TransactionResult> {
  const log = getLogger()
  const sdk = getCetusSdk()

  try {
    const lowerSqrtPrice = tickToSqrtPriceX64(tickLower)
    const upperSqrtPrice = tickToSqrtPriceX64(tickUpper)
    const { coinA, coinB } = ClmmPoolUtil.getCoinAmountFromLiquidity(
      new BN(liquidity.toString()),
      new BN(currentSqrtPrice.toString()),
      lowerSqrtPrice,
      upperSqrtPrice,
      true,
    )

    const slippageFactor = 1 - slippage
    const minAmountA = coinA.muln(Math.floor(slippageFactor * 1000)).divn(1000)
    const minAmountB = coinB.muln(Math.floor(slippageFactor * 1000)).divn(1000)

    log.info('Removing all liquidity', {
      positionId,
      liquidity: liquidity.toString(),
      minA: minAmountA.toString(),
      minB: minAmountB.toString(),
    })

    const payload = await sdk.Position.removeLiquidityTransactionPayload({
      coinTypeA,
      coinTypeB,
      delta_liquidity: liquidity.toString(),
      min_amount_a: minAmountA.toString(),
      min_amount_b: minAmountB.toString(),
      collect_fee: true,
      rewarder_coin_types: rewarderCoinTypes,
      pool_id: poolId,
      pos_id: positionId,
    })

    const result = await sendTx(payload, keypair, dryRunOnly)
    if (result.success) {
      log.info('Liquidity removed + fees collected', { positionId, digest: result.digest })
    }
    return result
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    log.error('Failed to remove liquidity', { positionId, error: msg })
    return { success: false, digest: null, gasCost: 0n, error: msg }
  }
}

export async function addLiquidity(
  poolId: string,
  positionId: string,
  coinTypeA: string,
  coinTypeB: string,
  tickLower: number,
  tickUpper: number,
  amountA: string,
  amountB: string,
  slippage: number,
  collectFee: boolean,
  keypair: Ed25519Keypair,
  dryRunOnly: boolean,
  rewarderCoinTypes: string[] = [],
  currentSqrtPrice?: bigint,
  decimalsA = 6,
  decimalsB = 9,
): Promise<TransactionResult> {
  const log = getLogger()
  const sdk = getCetusSdk()

  try {
    const preferFixA = currentSqrtPrice != null
      ? shouldFixAmountA(tickLower, tickUpper, currentSqrtPrice, amountA, amountB, decimalsA, decimalsB)
      : true

    // Try bottleneck token first; if it fails, retry with the other direction
    for (const fixA of [preferFixA, !preferFixA]) {
      log.info('Attempting addLiquidity', { positionId, fixA, amountA, amountB })

      const payload = await sdk.Position.createAddLiquidityFixTokenPayload({
        pool_id: poolId,
        coinTypeA,
        coinTypeB,
        tick_lower: tickLower,
        tick_upper: tickUpper,
        is_open: false,
        pos_id: positionId,
        fix_amount_a: fixA,
        amount_a: amountA,
        amount_b: amountB,
        slippage,
        collect_fee: collectFee,
        rewarder_coin_types: rewarderCoinTypes,
      })

      const result = await sendTx(payload, keypair, dryRunOnly)
      if (result.success) {
        log.info('Liquidity added', { positionId, fixA, digest: result.digest })
        return result
      }

      // If first attempt failed, try the other fix direction
      if (fixA === preferFixA) {
        log.warn(`addLiquidity with fix_amount_a=${fixA} failed, retrying with ${!fixA}`, {
          positionId,
          error: result.error,
        })
        continue
      }

      // Both failed
      return result
    }

    // Should not reach here
    return { success: false, digest: null, gasCost: 0n, error: 'Unexpected state' }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    log.error('Failed to add liquidity', { positionId, error: msg })
    return { success: false, digest: null, gasCost: 0n, error: msg }
  }
}
