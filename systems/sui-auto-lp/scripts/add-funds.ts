/**
 * Add idle wallet funds to an existing position.
 *
 * Steps:
 * 1. Calculate the deposit ratio for the current tick range
 * 2. Compare Aggregator vs Direct Pool swap quotes
 * 3. Execute swap (USDC → SUI) using the cheaper method
 * 4. Add liquidity to the existing position
 *
 * Usage:
 *   DRY_RUN=true  npx tsx scripts/add-funds.ts   # dry-run (default)
 *   DRY_RUN=false npx tsx scripts/add-funds.ts   # live execution
 *
 * TODO(#38):
 *   - POOL_ID / POSITION_ID がハードコードされている。
 *     リバランスで POSITION_ID が変わるため、実行前に state.json または
 *     GCE の state.json から最新の ID を確認・更新する必要がある。
 *   - .env の POOL_IDS / state.json から自動取得するように改修すべき。
 */

// Save CLI env vars before dotenv overwrites them
const CLI_DRY_RUN = process.env.DRY_RUN

import dotenv from 'dotenv'
dotenv.config({ override: true })

// Restore CLI override for DRY_RUN (dotenv override: true clobbers it)
if (CLI_DRY_RUN !== undefined) {
  process.env.DRY_RUN = CLI_DRY_RUN
}

import { initCetusSdk, initAggregatorClient, getPool } from '../src/core/pool.js'
import { calculateSwapPlan, executeSwap } from '../src/core/swap.js'
import { addLiquidity } from '../src/core/position.js'
import { initSuiClient, getSuiClient } from '../src/utils/sui.js'
import { loadKeypair } from '../src/utils/wallet.js'
import { initLogger } from '../src/utils/logger.js'

// FIXME(#38): ハードコード値。リバランスで POSITION_ID は変わるため、
// 実行前に GCE の state.json で最新 ID を確認すること。
// 将来的には .env POOL_IDS + state.json から自動解決に改修する。
const POOL_ID = '0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105'
const POSITION_ID = '0x9d8b51f27142dd84f70f721674aaaf6c47d62cfe7ae9701f586a2e39099fbdc6'
const DECIMALS_A = 6 // USDC
const DECIMALS_B = 9 // SUI
const GAS_RESERVE = 1_000_000_000n // 1.0 SUI

async function main() {
  const dryRun = process.env.DRY_RUN !== 'false'
  const slippage = parseFloat(process.env.SLIPPAGE_TOLERANCE ?? '0.01')
  const maxSwapCostPct = parseFloat(process.env.MAX_SWAP_COST_PCT ?? '0.01')

  initLogger(process.env.LOG_LEVEL ?? 'info')
  const suiClient = initSuiClient('mainnet')
  const keypair = loadKeypair(process.env.SUI_PRIVATE_KEY!)
  const wallet = keypair.getPublicKey().toSuiAddress()

  initCetusSdk('mainnet', wallet)
  initAggregatorClient('mainnet', suiClient)

  console.log(`\n=== Add Funds to Position ===`)
  console.log(`Mode: ${dryRun ? 'DRY RUN' : '🔴 LIVE'}`)
  console.log(`Wallet: ${wallet}`)
  console.log(`Position: ${POSITION_ID.slice(0, 16)}...`)

  // 1. Get pool info
  const pool = await getPool(POOL_ID)
  console.log(`\nPool tick: ${pool.currentTickIndex}, fee: ${(pool.feeRate / 10000).toFixed(2)}%`)

  // 2. Get position info to find tick range
  const sdk = (await import('../src/core/pool.js')).getCetusSdk()
  const pos = await sdk.Position.getPositionById(POSITION_ID)
  const tickLower = Number(pos.tick_lower_index)
  const tickUpper = Number(pos.tick_upper_index)
  console.log(`Position range: ${tickLower} - ${tickUpper}`)

  // 3. Get wallet balances
  const balances = await suiClient.getAllBalances({ owner: wallet })
  // Native USDC on Sui: 0xdba34672...::usdc::USDC
  const usdcBalance = balances.find(b =>
    b.coinType.includes('dba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC')
  )
  const suiBalance = balances.find(b => b.coinType === '0x2::sui::SUI')

  const rawA = BigInt(usdcBalance?.totalBalance ?? '0')
  const rawB = BigInt(suiBalance?.totalBalance ?? '0')

  console.log(`\nWallet balances:`)
  console.log(`  USDC: ${(Number(rawA) / 1e6).toFixed(2)}`)
  console.log(`  SUI:  ${(Number(rawB) / 1e9).toFixed(4)} (reserve ${Number(GAS_RESERVE) / 1e9} SUI for gas)`)

  const availableB = rawB > GAS_RESERVE ? rawB - GAS_RESERVE : 0n
  console.log(`  SUI available: ${(Number(availableB) / 1e9).toFixed(4)}`)

  if (rawA === 0n && availableB === 0n) {
    console.log('\nNo idle funds to add. Exiting.')
    return
  }

  // 4. Calculate swap plan
  const plan = calculateSwapPlan(pool, tickLower, tickUpper, rawA, availableB, DECIMALS_A, DECIMALS_B)
  console.log(`\nSwap plan: ${plan.reason}`)
  console.log(`  Need swap: ${plan.needSwap}`)
  console.log(`  Target USDC: ${(Number(plan.targetAmountA) / 1e6).toFixed(2)}`)
  console.log(`  Target SUI:  ${(Number(plan.targetAmountB) / 1e9).toFixed(4)}`)

  // 5. Execute swap if needed
  if (plan.needSwap) {
    console.log(`\n--- Swap Phase ---`)
    console.log(`Direction: ${plan.a2b ? 'USDC → SUI' : 'SUI → USDC'}`)
    console.log(`Amount: ${plan.a2b
      ? (Number(plan.swapAmount) / 1e6).toFixed(2) + ' USDC'
      : (Number(plan.swapAmount) / 1e9).toFixed(4) + ' SUI'
    }`)

    const swapResult = await executeSwap(pool, plan.a2b, plan.swapAmount, slippage, keypair, dryRun, maxSwapCostPct)

    if (!swapResult.success) {
      console.error(`\n❌ Swap failed: ${swapResult.error}`)
      return
    }
    console.log(`✅ Swap ${dryRun ? 'dry-run' : 'executed'}: ${swapResult.digest ?? '(dry-run)'}`)

    if (!dryRun) {
      // Wait for balance to settle
      console.log('Waiting 3s for balance settlement...')
      await new Promise(r => setTimeout(r, 3000))
    }
  }

  // 6. Re-fetch balances after swap (or use targets if dry-run)
  let finalA: bigint, finalB: bigint
  if (!dryRun && plan.needSwap) {
    const newBalances = await suiClient.getAllBalances({ owner: wallet })
    const newUsdc = newBalances.find(b =>
      b.coinType.includes('dba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC')
    )
    const newSui = newBalances.find(b => b.coinType === '0x2::sui::SUI')
    finalA = BigInt(newUsdc?.totalBalance ?? '0')
    const rawFinalB = BigInt(newSui?.totalBalance ?? '0')
    finalB = rawFinalB > GAS_RESERVE ? rawFinalB - GAS_RESERVE : 0n
  } else {
    finalA = plan.targetAmountA
    finalB = plan.targetAmountB
  }

  console.log(`\n--- Add Liquidity Phase ---`)
  console.log(`  USDC: ${(Number(finalA) / 1e6).toFixed(2)}`)
  console.log(`  SUI:  ${(Number(finalB) / 1e9).toFixed(4)}`)

  // 7. Add liquidity
  const result = await addLiquidity(
    POOL_ID,
    POSITION_ID,
    pool.coinTypeA,
    pool.coinTypeB,
    tickLower,
    tickUpper,
    finalA.toString(),
    finalB.toString(),
    slippage,
    false, // don't collect fees (separate concern)
    keypair,
    dryRun,
    pool.rewarderCoinTypes,
  )

  if (result.success) {
    console.log(`\n✅ Liquidity ${dryRun ? 'dry-run passed' : 'added'}!`)
    console.log(`   Digest: ${result.digest ?? '(dry-run)'}`)
  } else {
    console.error(`\n❌ addLiquidity failed: ${result.error}`)
  }
}

main().catch(err => {
  console.error('Fatal error:', err)
  process.exit(1)
})
