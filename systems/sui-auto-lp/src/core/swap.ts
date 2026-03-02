import { ClmmPoolUtil } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { Transaction } from '@mysten/sui/transactions'
import BN from 'bn.js'
import type { PoolInfo, TransactionResult } from '../types/index.js'
import { getCetusSdk, getAggregatorClient } from './pool.js'
import { getSuiClient, clampGasCost } from '../utils/sui.js'
import { tickToSqrtPriceX64, coinBPriceInCoinA, getCurrentPrice } from './price.js'
import { getLogger } from '../utils/logger.js'
import type { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'

interface SwapPlan {
  needSwap: boolean
  a2b: boolean // true: USDC→SUI, false: SUI→USDC
  swapAmount: bigint
  reason: string
  targetAmountA: bigint // USDC target
  targetAmountB: bigint // SUI target
}

/**
 * Calculate the optimal token ratio for a given tick range,
 * then determine how much to swap to reach that ratio.
 */
export function calculateSwapPlan(
  pool: PoolInfo,
  tickLower: number,
  tickUpper: number,
  balanceA: bigint, // USDC raw balance
  balanceB: bigint, // SUI raw balance
  decimalsA: number,
  decimalsB: number,
): SwapPlan {
  const log = getLogger()
  const curSqrtPrice = new BN(pool.currentSqrtPrice.toString())

  // Get the deposit ratio for the target range
  const { ratioA, ratioB } = ClmmPoolUtil.calculateDepositRatioFixTokenA(
    tickLower,
    tickUpper,
    curSqrtPrice,
  )

  const ratioANum = ratioA.toNumber()
  const ratioBNum = ratioB.toNumber()

  log.info('Deposit ratio calculated', {
    ratioA: ratioANum.toFixed(6),
    ratioB: ratioBNum.toFixed(6),
    tickLower,
    tickUpper,
  })

  // coinB (SUI) price in coinA (USDC) — i.e. how many USDC per 1 SUI
  const suiPriceInUsdc = coinBPriceInCoinA(pool, decimalsA, decimalsB)

  const valueA = Number(balanceA) / (10 ** decimalsA) // USDC value
  const valueB = (Number(balanceB) / (10 ** decimalsB)) * suiPriceInUsdc // SUI value in USDC

  const totalValue = valueA + valueB

  // Target values based on deposit ratio
  const targetValueA = totalValue * ratioANum / (ratioANum + ratioBNum)
  const targetValueB = totalValue * ratioBNum / (ratioANum + ratioBNum)

  const diffA = targetValueA - valueA // positive = need more USDC
  const diffB = targetValueB - valueB // positive = need more SUI

  log.info('Balance analysis', {
    currentA_USDC: valueA.toFixed(2),
    currentB_USD: valueB.toFixed(2),
    targetA_USDC: targetValueA.toFixed(2),
    targetB_USD: targetValueB.toFixed(2),
    suiPriceUsdc: suiPriceInUsdc.toFixed(4),
  })

  // Minimum swap threshold: $1 to avoid wasting gas
  if (Math.abs(diffA) < 1) {
    return {
      needSwap: false,
      a2b: false,
      swapAmount: 0n,
      reason: `Imbalance too small ($${Math.abs(diffA).toFixed(2)}), skip swap`,
      targetAmountA: BigInt(Math.floor(targetValueA * (10 ** decimalsA))),
      targetAmountB: BigInt(Math.floor((targetValueB / suiPriceInUsdc) * (10 ** decimalsB))),
    }
  }

  if (diffA > 0) {
    // Need more USDC → swap SUI to USDC (a2b=false, SUI→USDC)
    const swapSuiAmount = diffA / suiPriceInUsdc // SUI amount to swap
    const swapRaw = BigInt(Math.floor(swapSuiAmount * (10 ** decimalsB)))
    return {
      needSwap: true,
      a2b: false, // SUI(B) → USDC(A)
      swapAmount: swapRaw,
      reason: `Swap ${swapSuiAmount.toFixed(4)} SUI → USDC ($${diffA.toFixed(2)})`,
      targetAmountA: BigInt(Math.floor(targetValueA * (10 ** decimalsA))),
      targetAmountB: BigInt(Math.floor((targetValueB / suiPriceInUsdc) * (10 ** decimalsB))),
    }
  } else {
    // Need more SUI → swap USDC to SUI (a2b=true, USDC→SUI)
    const swapUsdcAmount = Math.abs(diffA) // USDC amount to swap
    const swapRaw = BigInt(Math.floor(swapUsdcAmount * (10 ** decimalsA)))
    return {
      needSwap: true,
      a2b: true, // USDC(A) → SUI(B)
      swapAmount: swapRaw,
      reason: `Swap ${swapUsdcAmount.toFixed(4)} USDC → SUI ($${Math.abs(diffA).toFixed(2)})`,
      targetAmountA: BigInt(Math.floor(targetValueA * (10 ** decimalsA))),
      targetAmountB: BigInt(Math.floor((targetValueB / suiPriceInUsdc) * (10 ** decimalsB))),
    }
  }
}

/**
 * Execute a swap by comparing Aggregator and Direct Pool quotes,
 * then executing via the method that yields more output.
 */
const DECIMALS_A = 6 // USDC
const DECIMALS_B = 9 // SUI

export async function executeSwap(
  pool: PoolInfo,
  a2b: boolean,
  amount: bigint,
  slippage: number,
  keypair: Ed25519Keypair,
  dryRunOnly: boolean,
  maxSwapCostPct?: number,
): Promise<TransactionResult> {
  const log = getLogger()
  const sdk = getCetusSdk()

  // Fetch quotes from both sources in parallel
  const [aggQuote, directQuote] = await Promise.all([
    getAggregatorQuote(pool, a2b, amount).catch(err => {
      log.warn('Aggregator quote failed', { error: err instanceof Error ? err.message : String(err) })
      return null
    }),
    getDirectPoolQuote(sdk, pool, a2b, amount, DECIMALS_A, DECIMALS_B).catch(err => {
      log.warn('Direct Pool quote failed', { error: err instanceof Error ? err.message : String(err) })
      return null
    }),
  ])

  // Determine which method gives better output
  const aggOut = aggQuote?.amountOut ?? 0n
  const directOut = directQuote?.estimatedOut ?? 0n
  const winner = aggOut > directOut ? 'aggregator' : aggOut < directOut ? 'direct_pool' : 'tie'

  // Calculate advantage of winner over loser in basis points
  const baseOut = winner === 'aggregator' ? directOut : aggOut
  const diffBps = baseOut > 0n
    ? Number((aggOut - directOut) * 10000n / baseOut) // positive = aggregator better
    : 0

  log.info('Swap quote comparison', {
    a2b,
    amountIn: amount.toString(),
    aggregatorOut: aggOut.toString(),
    directPoolOut: directOut.toString(),
    diffBps,  // positive = aggregator better, negative = direct pool better
    winner,
    aggRoute: aggQuote?.pathSummary ?? 'N/A',
  })

  // --- Effective cost guard: abort if spread is too wide ---
  if (maxSwapCostPct != null && maxSwapCostPct > 0) {
    const bestOut = aggOut > directOut ? aggOut : directOut
    if (bestOut > 0n) {
      // Fair output from pool's currentSqrtPrice
      // getCurrentPrice returns coinB/coinA (SUI per USDC)
      const rawPrice = getCurrentPrice(pool, DECIMALS_A, DECIMALS_B)
      const poolFeeRate = pool.feeRate / 1_000_000

      let fairOutput: number
      if (a2b) {
        // USDC → SUI: amountIn USDC × (SUI per USDC) × (1 - fee)
        fairOutput = (Number(amount) / 1e6) * rawPrice * (1 - poolFeeRate) * 1e9
      } else {
        // SUI → USDC: amountIn SUI × (USDC per SUI) × (1 - fee)
        fairOutput = (Number(amount) / 1e9) * (1 / rawPrice) * (1 - poolFeeRate) * 1e6
      }

      const effectiveCostPct = 1 - (Number(bestOut) / fairOutput)

      log.info('Swap effective cost check', {
        fairOutput: fairOutput.toFixed(0),
        bestQuoteOutput: bestOut.toString(),
        effectiveCostPct: (effectiveCostPct * 100).toFixed(3) + '%',
        maxAllowed: (maxSwapCostPct * 100).toFixed(1) + '%',
      })

      if (effectiveCostPct > maxSwapCostPct) {
        const msg = `Swap aborted: effective cost ${(effectiveCostPct * 100).toFixed(2)}% exceeds max ${(maxSwapCostPct * 100).toFixed(1)}%`
        log.error(msg, { a2b, amountIn: amount.toString(), bestOut: bestOut.toString(), fairOutput: fairOutput.toFixed(0) })
        return { success: false, digest: null, gasCost: 0n, error: msg }
      }
    }
  }

  // Try the better method first, fall back to the other on failure
  let method: 'aggregator' | 'direct_pool' = winner === 'direct_pool' ? 'direct_pool' : 'aggregator'

  if (method === 'aggregator' && aggQuote) {
    const result = await executeSwapViaAggregator(pool, a2b, amount, slippage, keypair, dryRunOnly, aggQuote)
    if (result.success) {
      log.info('Swap executed', { method: 'aggregator', fallback: false, diffBps })
      return result
    }

    log.warn('Aggregator execution failed, falling back to Direct Pool', { error: result.error })
    if (directQuote) {
      const fbResult = await executeSwapDirectPool(pool, a2b, amount, slippage, keypair, dryRunOnly, directQuote)
      if (fbResult.success) log.info('Swap executed', { method: 'direct_pool', fallback: true, diffBps })
      return fbResult
    }
    return result
  } else if (directQuote) {
    const result = await executeSwapDirectPool(pool, a2b, amount, slippage, keypair, dryRunOnly, directQuote)
    if (result.success) {
      log.info('Swap executed', { method: 'direct_pool', fallback: false, diffBps })
      return result
    }

    log.warn('Direct Pool execution failed, falling back to Aggregator', { error: result.error })
    if (aggQuote) {
      const fbResult = await executeSwapViaAggregator(pool, a2b, amount, slippage, keypair, dryRunOnly, aggQuote)
      if (fbResult.success) log.info('Swap executed', { method: 'aggregator', fallback: true, diffBps })
      return fbResult
    }
    return result
  }

  return { success: false, digest: null, gasCost: 0n, error: 'Both Aggregator and Direct Pool quotes failed' }
}

// --- Quote types ---

interface AggregatorQuote {
  amountOut: bigint
  routers: any // RouterDataV3
  pathSummary: string
}

interface DirectPoolQuote {
  estimatedOut: bigint
  amountLimit: bigint
  poolData: any
  feeAmount: string
}

async function getAggregatorQuote(
  pool: PoolInfo,
  a2b: boolean,
  amount: bigint,
): Promise<AggregatorQuote> {
  const aggClient = getAggregatorClient()
  const from = a2b ? pool.coinTypeA : pool.coinTypeB
  const target = a2b ? pool.coinTypeB : pool.coinTypeA

  const routers = await aggClient.findRouters({
    from,
    target,
    amount: new BN(amount.toString()),
    byAmountIn: true,
  })

  if (!routers || routers.insufficientLiquidity) {
    throw new Error(routers?.insufficientLiquidity ? 'insufficient liquidity' : 'no route found')
  }

  const pathSummary = routers.paths.map(p => {
    const steps = (p as any).steps?.map((s: any) => s.dex ?? s.provider ?? 'unknown') ?? ['unknown']
    return steps.join('→')
  }).join(' | ')

  return {
    amountOut: BigInt(routers.amountOut.toString()),
    routers,
    pathSummary,
  }
}

async function getDirectPoolQuote(
  sdk: ReturnType<typeof getCetusSdk>,
  pool: PoolInfo,
  a2b: boolean,
  amount: bigint,
  decimalsA: number,
  decimalsB: number,
): Promise<DirectPoolQuote> {
  const poolData = await sdk.Pool.getPool(pool.poolId)
  const preSwapResult = await sdk.Swap.preswap({
    pool: poolData,
    currentSqrtPrice: pool.currentSqrtPrice.toString() as any,
    decimalsA,
    decimalsB,
    a2b,
    byAmountIn: true,
    amount: amount.toString(),
    coinTypeA: pool.coinTypeA,
    coinTypeB: pool.coinTypeB,
  })

  if (!preSwapResult || preSwapResult.isExceed) {
    throw new Error(preSwapResult?.isExceed ? 'exceeds pool liquidity' : 'preswap failed')
  }

  const estimatedOut = BigInt(preSwapResult.estimatedAmountOut)

  return {
    estimatedOut,
    amountLimit: estimatedOut,
    poolData,
    feeAmount: preSwapResult.estimatedFeeAmount?.toString() ?? '0',
  }
}

/**
 * Execute a swap via Cetus Aggregator SDK using a pre-fetched quote.
 */
async function executeSwapViaAggregator(
  pool: PoolInfo,
  a2b: boolean,
  amount: bigint,
  slippage: number,
  keypair: Ed25519Keypair,
  dryRunOnly: boolean,
  quote: AggregatorQuote,
): Promise<TransactionResult> {
  const log = getLogger()

  try {
    const aggClient = getAggregatorClient()
    const client = getSuiClient()
    const sender = keypair.getPublicKey().toSuiAddress()

    log.info('Executing via Aggregator', {
      a2b,
      amountIn: amount.toString(),
      amountOut: quote.amountOut.toString(),
      route: quote.pathSummary,
    })

    // Build swap transaction via fastRouterSwap
    const txb = new Transaction()
    txb.setSender(sender)
    await aggClient.fastRouterSwap({
      router: quote.routers,
      slippage,
      txb: txb as any,
      refreshAllCoins: true,
    })

    // Dry-run
    const txBytes = await txb.build({ client })
    const dryResult = await client.dryRunTransactionBlock({
      transactionBlock: Buffer.from(txBytes).toString('base64'),
    })

    const status = dryResult.effects.status.status
    const gasCost = clampGasCost(dryResult.effects.gasUsed)

    if (status !== 'success') {
      const error = dryResult.effects.status.error ?? 'Aggregator swap dry-run failed'
      log.warn('Aggregator swap dry-run failed', { error })
      return { success: false, digest: null, gasCost, error }
    }

    if (dryRunOnly) {
      log.info('Aggregator swap dry-run passed', {
        a2b,
        amount: amount.toString(),
        gasCost: gasCost.toString(),
        route: quote.pathSummary,
      })
      return { success: true, digest: null, gasCost, error: null }
    }

    // Execute
    const signedTx = await keypair.signTransaction(txBytes)
    const response = await client.executeTransactionBlock({
      transactionBlock: signedTx.bytes,
      signature: signedTx.signature,
      options: { showEffects: true },
    })

    if (!response) {
      return { success: false, digest: null, gasCost: 0n, error: 'Aggregator sendTransaction returned undefined' }
    }

    log.info('Aggregator swap executed', {
      digest: response.digest,
      a2b,
      amount: amount.toString(),
      route: quote.pathSummary,
    })
    const effects = response.effects!
    const finalGas = clampGasCost(effects.gasUsed)
    return { success: true, digest: response.digest, gasCost: finalGas, error: null }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    log.error('Aggregator swap failed', { error: msg })
    return { success: false, digest: null, gasCost: 0n, error: `Aggregator: ${msg}` }
  }
}

/**
 * Execute a swap directly on the pool using Cetus CLMM SDK.
 */
async function executeSwapDirectPool(
  pool: PoolInfo,
  a2b: boolean,
  amount: bigint,
  slippage: number,
  keypair: Ed25519Keypair,
  dryRunOnly: boolean,
  quote: DirectPoolQuote,
): Promise<TransactionResult> {
  const log = getLogger()
  const sdk = getCetusSdk()

  try {
    // Calculate amount_limit with slippage
    const amountLimit = quote.estimatedOut * BigInt(Math.floor((1 - slippage) * 10000)) / 10000n

    log.info('Executing via Direct Pool', {
      a2b,
      amountIn: amount.toString(),
      estimatedOut: quote.estimatedOut.toString(),
      amountLimit: amountLimit.toString(),
      feeAmount: quote.feeAmount,
    })

    // Create swap transaction
    const payload = await sdk.Swap.createSwapTransactionPayload({
      pool_id: pool.poolId,
      coinTypeA: pool.coinTypeA,
      coinTypeB: pool.coinTypeB,
      a2b,
      by_amount_in: true,
      amount: amount.toString(),
      amount_limit: amountLimit.toString(),
    })

    // Dry-run
    const txBytes = await (payload as any).build({ client: sdk.fullClient })
    const dryResult = await sdk.fullClient.dryRunTransactionBlock({
      transactionBlock: Buffer.from(txBytes).toString('base64'),
    })

    const status = dryResult.effects.status.status
    const gasCost = clampGasCost(dryResult.effects.gasUsed)

    if (status !== 'success') {
      const error = dryResult.effects.status.error ?? 'Swap dry-run failed'
      log.warn('Direct Pool swap dry-run failed', { error })
      return { success: false, digest: null, gasCost, error }
    }

    if (dryRunOnly) {
      log.info('Direct Pool swap dry-run passed', { a2b, amount: amount.toString(), gasCost: gasCost.toString() })
      return { success: true, digest: null, gasCost, error: null }
    }

    // Execute
    const response = await sdk.fullClient.sendTransaction(keypair as any, payload)
    if (!response) {
      return { success: false, digest: null, gasCost: 0n, error: 'Swap sendTransaction returned undefined' }
    }

    log.info('Direct Pool swap executed', { digest: response.digest, a2b, amount: amount.toString() })
    const effects = response.effects!
    const finalGas = clampGasCost(effects.gasUsed)
    return { success: true, digest: response.digest, gasCost: finalGas, error: null }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    log.error('Direct Pool swap failed', { error: msg })
    return { success: false, digest: null, gasCost: 0n, error: msg }
  }
}
