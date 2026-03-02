/**
 * Deploy LP Position (全額 or 指定額)
 *
 * ウォレットの残高を使って新しいCLMMポジションを開設する。
 * 1. プール状態を取得し、最適レンジ（±6% dynamic）を計算
 * 2. deposit ratio に基づいて SUI → USDC スワップ（Aggregator vs Direct Pool 自動比較）
 * 3. 新ポジション開設
 * 4. 結果表示（新ポジションID）
 *
 * Usage:
 *   npx tsx scripts/deploy-position.ts --dry-run   # シミュレーション
 *   npx tsx scripts/deploy-position.ts              # 本番実行
 */
import { initCetusSDK, TickMath, ClmmPoolUtil, d } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { SuiClient, getFullnodeUrl } from '@mysten/sui/client'
import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'
import BN from 'bn.js'
import 'dotenv/config'
import { initCetusSdk, initAggregatorClient, getPool } from '../src/core/pool.js'
import { executeSwap } from '../src/core/swap.js'
import { openPosition } from '../src/core/position.js'
import { initSuiClient } from '../src/utils/sui.js'
import { initLogger, getLogger } from '../src/utils/logger.js'

const POOL_ID = '0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105'
const COIN_TYPE_A = '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC'
const COIN_TYPE_B = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
const DA = 6, DB = 9
const TICK_SPACING = 60

const TARGET_USD = 0 // 0 = use all available funds
const RANGE_PCT = 0.03 // ±3% (narrow range for low volatility)
const GAS_RESERVE_SUI = 1_000_000_000n // 1 SUI reserved for gas

async function main() {
  const DRY_RUN = process.argv.includes('--dry-run')
  console.log(`Mode: ${DRY_RUN ? 'DRY-RUN' : 'LIVE EXECUTION'}`)
  console.log()

  // --- Init ---
  const privateKey = process.env.SUI_PRIVATE_KEY
  if (!privateKey) throw new Error('SUI_PRIVATE_KEY not set')

  const keypair = Ed25519Keypair.fromSecretKey(privateKey)
  const address = keypair.getPublicKey().toSuiAddress()
  const client = new SuiClient({ url: getFullnodeUrl('mainnet') })
  const sdk = initCetusSDK({ network: 'mainnet' })
  sdk.senderAddress = address

  // Init project SDK singletons for executeSwap
  initLogger('info')
  initSuiClient('mainnet')
  initCetusSdk('mainnet', address)
  initAggregatorClient('mainnet', client)

  console.log(`Wallet: ${address}`)

  // --- Pool state ---
  const pool = await sdk.Pool.getPool(POOL_ID)
  const suiPriceRaw = TickMath.sqrtPriceX64ToPrice(
    new BN(pool.current_sqrt_price.toString()), DA, DB
  ).toNumber()
  const suiPrice = 1 / suiPriceRaw
  const currentTick = pool.current_tick_index
  console.log(`SUI price: $${suiPrice.toFixed(4)} (tick ${currentTick})`)

  // --- Wallet balances ---
  const [balA, balB] = await Promise.all([
    client.getBalance({ owner: address, coinType: COIN_TYPE_A }),
    client.getBalance({ owner: address, coinType: COIN_TYPE_B }),
  ])
  const walletUsdc = BigInt(balA.totalBalance)
  const walletSui = BigInt(balB.totalBalance)
  const totalValueUsd = Number(walletUsdc) / 1e6 + (Number(walletSui) / 1e9) * suiPrice
  console.log(`Wallet: ${(Number(walletUsdc) / 1e6).toFixed(2)} USDC + ${(Number(walletSui) / 1e9).toFixed(2)} SUI ($${totalValueUsd.toFixed(2)})`)

  // Subtract reserves from deployable value
  const reserveValueUsd = (Number(GAS_RESERVE_SUI) / 1e9) * suiPrice
  const deployableValueUsd = totalValueUsd - reserveValueUsd
  console.log(`Reserves: ${(Number(GAS_RESERVE_SUI) / 1e9).toFixed(0)} SUI gas = $${reserveValueUsd.toFixed(2)}`)

  const MIN_DEPLOY_USD = 20 // minimum $20 to deploy (covers both sides + gas)
  if (deployableValueUsd < MIN_DEPLOY_USD) {
    throw new Error(`Insufficient funds after reserves: $${deployableValueUsd.toFixed(2)} (need >=$${MIN_DEPLOY_USD}, total $${totalValueUsd.toFixed(2)}, reserves $${reserveValueUsd.toFixed(2)})`)
  }

  const effectiveTarget = TARGET_USD > 0
    ? Math.min(TARGET_USD, deployableValueUsd)
    : deployableValueUsd
  console.log(`Effective target: $${effectiveTarget.toFixed(2)}${TARGET_USD === 0 ? ' (all funds minus reserves)' : ''}`)

  // --- Calculate optimal range ---
  const priceLower = suiPrice * (1 - RANGE_PCT)
  const priceUpper = suiPrice * (1 + RANGE_PCT)

  // Use TickMath for accurate conversion (note: priceToTickIndex expects SUI/USDC)
  const rawLower = 1 / priceUpper
  const rawUpper = 1 / priceLower
  const tickLower = Math.round(TickMath.priceToTickIndex(
    d(rawLower), DA, DB
  ) / TICK_SPACING) * TICK_SPACING
  const tickUpper = Math.round(TickMath.priceToTickIndex(
    d(rawUpper), DA, DB
  ) / TICK_SPACING) * TICK_SPACING

  const actualLowerRaw = TickMath.tickIndexToPrice(tickLower, DA, DB).toNumber()
  const actualUpperRaw = TickMath.tickIndexToPrice(tickUpper, DA, DB).toNumber()
  const actualLower = 1 / actualUpperRaw
  const actualUpper = 1 / actualLowerRaw
  console.log()
  console.log(`Range: $${actualLower.toFixed(4)} ~ $${actualUpper.toFixed(4)} (±${(RANGE_PCT * 100).toFixed(0)}%)`)
  console.log(`Ticks: [${tickLower}, ${tickUpper}]`)

  // --- Calculate deposit ratio ---
  const curSqrtPrice = new BN(pool.current_sqrt_price.toString())
  const { ratioA, ratioB } = ClmmPoolUtil.calculateDepositRatioFixTokenA(
    tickLower, tickUpper, curSqrtPrice
  )
  const rA = ratioA.toNumber()
  const rB = ratioB.toNumber()
  const pctA = (rA / (rA + rB) * 100).toFixed(1)
  const pctB = (rB / (rA + rB) * 100).toFixed(1)
  console.log(`Deposit ratio: USDC ${pctA}% / SUI ${pctB}%`)

  // --- Determine amounts ---
  const availableSui = walletSui > GAS_RESERVE_SUI ? walletSui - GAS_RESERVE_SUI : 0n
  const availableUsdc = walletUsdc

  // Calculate how much USDC vs SUI we need
  const targetUsdcValue = effectiveTarget * rA / (rA + rB) // USD worth of USDC needed
  const targetSuiValue = effectiveTarget * rB / (rA + rB) // USD worth of SUI needed
  const existingUsdcValue = Number(walletUsdc) / 1e6
  const existingSuiValue = (Number(availableSui) / 1e9) * suiPrice
  const swapUsdcNeeded = targetUsdcValue - existingUsdcValue // positive = need more USDC
  const swapSuiNeeded = targetSuiValue - existingSuiValue // positive = need more SUI

  console.log()
  console.log(`Target USDC: $${targetUsdcValue.toFixed(2)} (have $${existingUsdcValue.toFixed(2)})`)
  console.log(`Target SUI:  $${targetSuiValue.toFixed(2)} (have $${existingSuiValue.toFixed(2)})`)

  // === Step 1: Swap to balance USDC/SUI ratio ===
  const poolInfo = await getPool(POOL_ID)
  // Track swap amounts for dry-run balance estimation
  let swapDeltaUsdc = 0n  // positive = gained USDC, negative = spent USDC
  let swapDeltaSui = 0n   // positive = gained SUI, negative = spent SUI

  if (swapUsdcNeeded > 1) {
    // Need more USDC → swap SUI → USDC
    console.log()
    console.log('=== Step 1/2: Swap SUI → USDC ===')
    const swapSuiUncapped = BigInt(Math.ceil(((swapUsdcNeeded / suiPrice) * 1.005) * 1e9))
    const swapSuiRaw = swapSuiUncapped > availableSui ? availableSui : swapSuiUncapped
    const swapSuiHuman = Number(swapSuiRaw) / 1e9
    if (swapSuiRaw < swapSuiUncapped) {
      console.log(`Swap capped by gas reserve: ${swapSuiHuman.toFixed(4)} SUI (wanted ${(Number(swapSuiUncapped) / 1e9).toFixed(4)})`)
    }
    console.log(`Swapping ${swapSuiHuman.toFixed(4)} SUI → ~$${swapUsdcNeeded.toFixed(2)} USDC`)

    const swapResult = await executeSwap(
      poolInfo,
      false, // a2b=false → SUI(B) → USDC(A)
      swapSuiRaw,
      0.01, // 1% slippage
      keypair,
      DRY_RUN,
    )

    if (!swapResult.success) {
      throw new Error(`Swap failed: ${swapResult.error}`)
    }
    console.log(`Swap ${DRY_RUN ? 'dry-run' : 'TX'}: ${swapResult.digest ?? 'OK'}`)
    // Estimate: spent SUI, gained USDC (approximate using price)
    swapDeltaSui = -swapSuiRaw
    swapDeltaUsdc = BigInt(Math.floor(swapUsdcNeeded * 1e6))

    if (!DRY_RUN) {
      console.log('Waiting 5s for state to settle...')
      await new Promise(r => setTimeout(r, 5000))
    }
  } else if (swapSuiNeeded > 1) {
    // Need more SUI → swap USDC → SUI
    console.log()
    console.log('=== Step 1/2: Swap USDC → SUI ===')
    const swapUsdcUncapped = BigInt(Math.ceil((swapSuiNeeded * 1.005) * 1e6))
    const swapUsdcRaw = swapUsdcUncapped > availableUsdc ? availableUsdc : swapUsdcUncapped
    const swapUsdcHuman = Number(swapUsdcRaw) / 1e6
    if (swapUsdcRaw < swapUsdcUncapped) {
      console.log(`Swap capped by compound reserve: ${swapUsdcHuman.toFixed(2)} USDC (wanted ${(Number(swapUsdcUncapped) / 1e6).toFixed(2)})`)
    }
    console.log(`Swapping ${swapUsdcHuman.toFixed(2)} USDC → ~$${swapSuiNeeded.toFixed(2)} worth of SUI`)

    const swapResult = await executeSwap(
      poolInfo,
      true, // a2b=true → USDC(A) → SUI(B)
      swapUsdcRaw,
      0.01, // 1% slippage
      keypair,
      DRY_RUN,
    )

    if (!swapResult.success) {
      throw new Error(`Swap failed: ${swapResult.error}`)
    }
    console.log(`Swap ${DRY_RUN ? 'dry-run' : 'TX'}: ${swapResult.digest ?? 'OK'}`)
    // Estimate: spent USDC, gained SUI (approximate using price)
    swapDeltaUsdc = -swapUsdcRaw
    swapDeltaSui = BigInt(Math.floor((swapSuiNeeded / suiPrice) * 1e9))

    if (!DRY_RUN) {
      console.log('Waiting 5s for state to settle...')
      await new Promise(r => setTimeout(r, 5000))
    }
  } else {
    console.log('\nStep 1/2: No swap needed (balanced)')
  }

  // === Refresh balances (or estimate for dry-run) ===
  let finalUsdc: bigint
  let finalSui: bigint
  if (DRY_RUN) {
    // In dry-run, swap didn't actually execute — estimate post-swap balances
    finalUsdc = walletUsdc + swapDeltaUsdc
    finalSui = walletSui + swapDeltaSui
    console.log(`\nPost-swap (estimated): ${(Number(finalUsdc) / 1e6).toFixed(2)} USDC + ${(Number(finalSui) / 1e9).toFixed(2)} SUI`)
  } else {
    const [balA2, balB2] = await Promise.all([
      client.getBalance({ owner: address, coinType: COIN_TYPE_A }),
      client.getBalance({ owner: address, coinType: COIN_TYPE_B }),
    ])
    finalUsdc = BigInt(balA2.totalBalance)
    finalSui = BigInt(balB2.totalBalance)
    console.log(`\nPost-swap: ${(Number(finalUsdc) / 1e6).toFixed(2)} USDC + ${(Number(finalSui) / 1e9).toFixed(2)} SUI`)
  }

  // === Step 2: Open LP Position ===
  console.log()
  console.log('=== Step 2/2: Open LP Position ===')

  const lpUsdcRaw = finalUsdc
  const reservedUsdc = 0n

  // Cap SUI for LP: remaining $value after USDC allocation, minus gas reserve
  const lpUsdcValue = Number(lpUsdcRaw) / 1e6
  const targetSuiValueLp = effectiveTarget - lpUsdcValue // remaining $value to fill with SUI
  const maxSuiForLp = targetSuiValueLp > 0 ? BigInt(Math.floor((targetSuiValueLp / suiPrice) * 1e9)) : 0n
  const availSui = finalSui > GAS_RESERVE_SUI ? finalSui - GAS_RESERVE_SUI : 0n
  const lpSuiRaw = availSui < maxSuiForLp ? availSui : maxSuiForLp
  const lpUsdc = lpUsdcRaw.toString()
  const lpSui = lpSuiRaw.toString()

  const lpSuiValue = (Number(lpSui) / 1e9) * suiPrice
  const reservedSui = finalSui - lpSuiRaw
  console.log(`LP input: ${(Number(lpUsdc) / 1e6).toFixed(2)} USDC + ${(Number(lpSui) / 1e9).toFixed(2)} SUI`)
  console.log(`LP value: ~$${(lpUsdcValue + lpSuiValue).toFixed(2)}`)
  console.log(`Reserved: ${(Number(reservedSui) / 1e9).toFixed(2)} SUI ($${((Number(reservedSui) / 1e9) * suiPrice).toFixed(2)}) + ${(Number(reservedUsdc) / 1e6).toFixed(2)} USDC`)

  if (DRY_RUN && (swapDeltaUsdc !== 0n || swapDeltaSui !== 0n)) {
    // In dry-run with swap, LP open can't be validated (on-chain balances unchanged)
    console.log()
    console.log('=== DRY-RUN COMPLETE ===')
    console.log('Swap dry-run passed. LP open skipped (requires actual swap first).')
    console.log('Run without --dry-run to execute.')
  } else {
    // Re-fetch pool to get fresh sqrtPrice
    const freshPool = await sdk.Pool.getPool(POOL_ID)
    const freshSqrtPrice = BigInt(freshPool.current_sqrt_price.toString())

    const openResult = await openPosition(
      POOL_ID,
      COIN_TYPE_A,
      COIN_TYPE_B,
      tickLower,
      tickUpper,
      lpUsdc,
      lpSui,
      0.01,
      keypair,
      DRY_RUN,
      [], // rewarderCoinTypes
      freshSqrtPrice,
      DA,
      DB,
    )

    if (!openResult.success) {
      throw new Error(`Open LP failed: ${openResult.error}`)
    }

    console.log(`Open LP ${DRY_RUN ? 'dry-run' : 'TX'}: ${openResult.digest ?? 'OK'}`)

    if (DRY_RUN) {
      console.log()
      console.log('=== DRY-RUN COMPLETE ===')
      console.log('Run without --dry-run to execute.')
  } else {
    // Find new position ID
    console.log('Waiting 5s to detect new position...')
    await new Promise(r => setTimeout(r, 5000))

    const positions = await sdk.Position.getPositionList(address, [POOL_ID])
    const newPos = positions.find(p =>
      p.tick_lower_index === tickLower &&
      p.tick_upper_index === tickUpper &&
      !new BN(p.liquidity.toString()).isZero()
    )

    console.log()
    console.log('=== POSITION DEPLOYED ===')
    if (newPos) {
      console.log(`Position ID: ${newPos.pos_object_id}`)
      console.log()
      console.log('Update .env POSITION_IDS to:')
      console.log(`POSITION_IDS=${newPos.pos_object_id}`)
    } else {
      console.log('Could not auto-detect position ID. Check on Cetus UI.')
    }
  }
  } // end of swap-or-no-swap LP block
}

main().catch(err => {
  console.error('FATAL:', err.message || err)
  process.exit(1)
})
