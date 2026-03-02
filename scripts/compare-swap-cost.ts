/**
 * Compare swap costs: Direct Pool Swap vs Cetus Aggregator
 *
 * Usage: npx tsx scripts/compare-swap-cost.ts [amountUsd]
 *   amountUsd: swap amount in USD (default: 1500)
 *
 * Tests both directions (USDC→SUI, SUI→USDC) at multiple amounts.
 * No actual swaps executed — read-only estimation only.
 */
import { initCetusSDK, TickMath } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { AggregatorClient, Env } from '@cetusprotocol/aggregator-sdk'
import { SuiClient, getFullnodeUrl } from '@mysten/sui/client'
import BN from 'bn.js'
import 'dotenv/config'

const POOL_ID = '0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105'
const USDC_TYPE = '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC'
const SUI_TYPE = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
const DA = 6, DB = 9

interface QuoteResult {
  method: string
  direction: string
  amountIn: string
  amountOut: string
  effectiveRate: number
  feeEstimate: string
  latencyMs: number
}

async function getDirectPoolQuote(
  sdk: ReturnType<typeof initCetusSDK>,
  pool: any,
  a2b: boolean,
  amount: bigint,
  suiPrice: number,
): Promise<QuoteResult> {
  const start = Date.now()

  const preSwap = await sdk.Swap.preswap({
    pool,
    currentSqrtPrice: Number(pool.current_sqrt_price),
    decimalsA: DA,
    decimalsB: DB,
    a2b,
    byAmountIn: true,
    amount: amount.toString(),
    coinTypeA: USDC_TYPE,
    coinTypeB: SUI_TYPE,
  })

  const latencyMs = Date.now() - start

  if (!preSwap || preSwap.isExceed) {
    return {
      method: 'Direct Pool',
      direction: a2b ? 'USDC→SUI' : 'SUI→USDC',
      amountIn: amount.toString(),
      amountOut: '0 (exceed)',
      effectiveRate: 0,
      feeEstimate: 'N/A',
      latencyMs,
    }
  }

  const amountOut = BigInt(preSwap.estimatedAmountOut)
  const feeAmount = preSwap.estimatedFeeAmount ?? '0'

  // Calculate effective rate in USD terms
  let inUsd: number, outUsd: number
  if (a2b) {
    // USDC→SUI: input USDC, output SUI
    inUsd = Number(amount) / 1e6
    outUsd = (Number(amountOut) / 1e9) * suiPrice
  } else {
    // SUI→USDC: input SUI, output USDC
    inUsd = (Number(amount) / 1e9) * suiPrice
    outUsd = Number(amountOut) / 1e6
  }

  const effectiveRate = (inUsd - outUsd) / inUsd // cost as % of input

  return {
    method: 'Direct Pool (0.25%)',
    direction: a2b ? 'USDC→SUI' : 'SUI→USDC',
    amountIn: a2b ? `${(Number(amount) / 1e6).toFixed(2)} USDC` : `${(Number(amount) / 1e9).toFixed(4)} SUI`,
    amountOut: a2b ? `${(Number(amountOut) / 1e9).toFixed(4)} SUI` : `${(Number(amountOut) / 1e6).toFixed(2)} USDC`,
    effectiveRate,
    feeEstimate: a2b ? `${(Number(feeAmount) / 1e6).toFixed(4)} USDC` : `${(Number(feeAmount) / 1e9).toFixed(6)} SUI`,
    latencyMs,
  }
}

async function getAggregatorQuote(
  aggClient: AggregatorClient,
  a2b: boolean,
  amount: bigint,
  suiPrice: number,
): Promise<QuoteResult> {
  const start = Date.now()

  const from = a2b ? USDC_TYPE : SUI_TYPE
  const target = a2b ? SUI_TYPE : USDC_TYPE

  const routers = await aggClient.findRouters({
    from,
    target,
    amount: new BN(amount.toString()),
    byAmountIn: true,
  })

  const latencyMs = Date.now() - start

  if (!routers || routers.insufficientLiquidity) {
    return {
      method: 'Aggregator',
      direction: a2b ? 'USDC→SUI' : 'SUI→USDC',
      amountIn: amount.toString(),
      amountOut: '0 (no route)',
      effectiveRate: 0,
      feeEstimate: 'N/A',
      latencyMs,
    }
  }

  const amountOut = BigInt(routers.amountOut.toString())

  // Parse route paths
  const pathSummary = routers.paths.map(p => {
    const providers = (p as any).steps?.map((s: any) =>
      s.dex ?? s.provider ?? 'unknown'
    ) ?? ['unknown']
    return providers.join('→')
  }).join(' | ')

  let inUsd: number, outUsd: number
  if (a2b) {
    inUsd = Number(amount) / 1e6
    outUsd = (Number(amountOut) / 1e9) * suiPrice
  } else {
    inUsd = (Number(amount) / 1e9) * suiPrice
    outUsd = Number(amountOut) / 1e6
  }

  const effectiveRate = (inUsd - outUsd) / inUsd

  return {
    method: `Aggregator [${pathSummary}]`,
    direction: a2b ? 'USDC→SUI' : 'SUI→USDC',
    amountIn: a2b ? `${(Number(amount) / 1e6).toFixed(2)} USDC` : `${(Number(amount) / 1e9).toFixed(4)} SUI`,
    amountOut: a2b ? `${(Number(amountOut) / 1e9).toFixed(4)} SUI` : `${(Number(amountOut) / 1e6).toFixed(2)} USDC`,
    effectiveRate,
    feeEstimate: `$${(inUsd * effectiveRate).toFixed(4)}`,
    latencyMs,
  }
}

async function main() {
  const customAmount = process.argv[2] ? parseFloat(process.argv[2]) : undefined
  const testAmountsUsd = customAmount ? [customAmount] : [100, 500, 1500]

  console.log('=== Swap Cost Comparison: Direct Pool vs Aggregator ===\n')

  // Initialize SDKs
  const client = new SuiClient({ url: getFullnodeUrl('mainnet') })
  const sdk = initCetusSDK({ network: 'mainnet' })

  const aggClient = new AggregatorClient({
    client,
    env: Env.Mainnet,
  })

  // Get pool data and current price
  const pool = await sdk.Pool.getPool(POOL_ID)
  const suiPriceRaw = TickMath.sqrtPriceX64ToPrice(
    new BN(pool.current_sqrt_price.toString()), DA, DB
  ).toNumber()
  const suiPrice = 1 / Math.max(suiPriceRaw, 1e-10)

  console.log(`SUI Price: $${suiPrice.toFixed(4)}`)
  console.log(`Pool Fee Rate: ${Number(pool.fee_rate) / 10000}%`)
  console.log()

  for (const amountUsd of testAmountsUsd) {
    console.log(`--- $${amountUsd} swap ---`)

    // Test both directions
    for (const a2b of [true, false]) {
      const direction = a2b ? 'USDC→SUI' : 'SUI→USDC'
      const rawAmount = a2b
        ? BigInt(Math.floor(amountUsd * 1e6)) // USDC
        : BigInt(Math.floor((amountUsd / suiPrice) * 1e9)) // SUI

      let direct: QuoteResult
      let agg: QuoteResult

      try {
        direct = await getDirectPoolQuote(sdk, pool, a2b, rawAmount, suiPrice)
      } catch (e) {
        direct = { method: 'Direct Pool', direction, amountIn: '', amountOut: `Error: ${e}`, effectiveRate: 0, feeEstimate: 'N/A', latencyMs: 0 }
      }

      try {
        agg = await getAggregatorQuote(aggClient, a2b, rawAmount, suiPrice)
      } catch (e) {
        agg = { method: 'Aggregator', direction, amountIn: '', amountOut: `Error: ${e}`, effectiveRate: 0, feeEstimate: 'N/A', latencyMs: 0 }
      }

      console.log(`\n  ${direction}:`)
      console.log(`    Direct Pool:  ${direct.amountIn} → ${direct.amountOut}  (cost: ${(direct.effectiveRate * 100).toFixed(4)}%, ${direct.latencyMs}ms)`)
      console.log(`    Aggregator:   ${agg.amountIn} → ${agg.amountOut}  (cost: ${(agg.effectiveRate * 100).toFixed(4)}%, ${agg.latencyMs}ms)`)

      if (direct.effectiveRate > 0 && agg.effectiveRate > 0) {
        const saving = direct.effectiveRate - agg.effectiveRate
        const savingPct = (saving / direct.effectiveRate) * 100
        const savingUsd = saving * amountUsd
        const better = saving > 0 ? 'Aggregator' : 'Direct Pool'
        console.log(`    → ${better} saves ${Math.abs(savingPct).toFixed(1)}% (≈$${Math.abs(savingUsd).toFixed(4)})`)
      }
    }
    console.log()
  }
}

main().catch(err => {
  console.error('Error:', err.message || err)
  process.exit(1)
})
