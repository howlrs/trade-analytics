/**
 * close-deepbook-long.ts
 *
 * DeepBook Margin の SUI ロングポジションをクローズし、利益確定するスクリプト。
 *
 * 2段階で実行:
 *   TX1: SUI売却 → USDC借入返済（debt清算）
 *   TX2: 残りSUI売却 → 全USDC引き出し
 *
 * Usage:
 *   npx tsx scripts/close-deepbook-long.ts --dry-run     # シミュレーション
 *   npx tsx scripts/close-deepbook-long.ts               # 実行
 *
 * 環境変数:
 *   SUI_PRIVATE_KEY               (必須)
 *   SUI_NETWORK                   (任意) mainnet | testnet
 *   DEEPBOOK_MARGIN_MANAGER_IDS   (必須) MarginManager ID
 */

import 'dotenv/config'
import { DeepBookClient, mainnetPackageIds } from '@mysten/deepbook-v3'
import { getFullnodeUrl, SuiClient } from '@mysten/sui/client'
import { Transaction } from '@mysten/sui/transactions'
import { initLogger } from '../src/utils/logger.js'
import { loadKeypair } from '../src/utils/wallet.js'

// ─── Patch outdated SDK constants ────────────────────────────────────────────
;(mainnetPackageIds as any).MARGIN_PACKAGE_ID = '0xfbd322126f1452fd4c89aedbaeb9fd0c44df9b5cedbe70d76bf80dc086031377'
;(mainnetPackageIds as any).LIQUIDATION_PACKAGE_ID = '0x55718c06706bee34c9f3c39f662f10be354a4dcc719699ad72091dc343b641b8'

// ─── Constants ───────────────────────────────────────────────────────────────

const TARGET_POOL_KEY = 'SUI_USDC'
const SUI_DECIMALS = 9
const USDC_DECIMALS = 6
const USDC_COIN_TYPE = '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmt(val: string | number, decimals = 4): string {
  const n = typeof val === 'string' ? parseFloat(val) : val
  if (isNaN(n)) return String(val)
  return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

function fmtUsd(val: number): string { return `$${fmt(val, 2)}` }

function separator(title: string) {
  console.log()
  console.log(`━━━ ${title} ${'━'.repeat(Math.max(0, 60 - title.length))}`)
}

async function buildAndDryRun(
  tx: Transaction,
  suiClient: SuiClient,
  address: string,
): Promise<{ success: boolean; gasUsed: number; error?: string; dryResult: any }> {
  tx.setSender(address)
  const txBytes = await tx.build({ client: suiClient })
  const dryResult = await suiClient.dryRunTransactionBlock({
    transactionBlock: Buffer.from(txBytes).toString('base64'),
  })
  const gasUsed = Number(
    BigInt(dryResult.effects.gasUsed.computationCost) +
    BigInt(dryResult.effects.gasUsed.storageCost) -
    BigInt(dryResult.effects.gasUsed.storageRebate)
  ) / 1e9
  const success = dryResult.effects.status.status === 'success'
  return { success, gasUsed, error: dryResult.effects.status.error, dryResult }
}

async function executeTransaction(
  tx: Transaction,
  suiClient: SuiClient,
  keypair: any,
): Promise<any> {
  const result = await suiClient.signAndExecuteTransaction({
    transaction: tx,
    signer: keypair,
    options: { showEffects: true, showBalanceChanges: true },
  })
  return result
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function main() {
  const dryRun = process.argv.includes('--dry-run')
  initLogger(process.env.LOG_LEVEL ?? 'warn')

  const privateKey = process.env.SUI_PRIVATE_KEY
  if (!privateKey) { console.error('SUI_PRIVATE_KEY not set'); process.exit(1) }

  const mmId = process.env.DEEPBOOK_MARGIN_MANAGER_IDS?.split(',')[0]?.trim()
  if (!mmId) { console.error('DEEPBOOK_MARGIN_MANAGER_IDS not set'); process.exit(1) }

  const network = (process.env.SUI_NETWORK ?? 'mainnet') as 'mainnet' | 'testnet'
  const keypair = loadKeypair(privateKey)
  const address = keypair.getPublicKey().toSuiAddress()
  const suiClient = new SuiClient({ url: getFullnodeUrl(network) })

  console.log(`DeepBook Margin — Close Long Position`)
  console.log(`Network: ${network} | Address: ${address}`)
  console.log(`Mode: ${dryRun ? 'DRY-RUN' : 'LIVE EXECUTION'}`)

  const client = new DeepBookClient({
    client: suiClient,
    address,
    env: network,
    marginManagers: {
      MM_0: { address: mmId, poolKey: TARGET_POOL_KEY },
    },
  })

  // ── 1. Current Position ──

  separator('Current Position')

  const midPrice = await client.midPrice(TARGET_POOL_KEY)
  const bookParams = await client.poolBookParams(TARGET_POOL_KEY)
  console.log(`Mid Price: ${fmt(midPrice)} | Lot Size: ${bookParams.lotSize}`)

  let state: any
  try { state = await client.getMarginManagerState('MM_0') }
  catch (e: any) { console.error(`Failed: ${e.message}`); process.exit(1) }

  const baseAsset = parseFloat(state.baseAsset)
  const quoteAsset = parseFloat(state.quoteAsset)
  const baseDebt = parseFloat(state.baseDebt)
  const quoteDebt = parseFloat(state.quoteDebt)
  const basePyth = parseFloat(state.basePythPrice) / (10 ** state.basePythDecimals)
  const quotePyth = parseFloat(state.quotePythPrice) / (10 ** state.quotePythDecimals)
  const totalAssetsUsd = baseAsset * basePyth + quoteAsset * quotePyth
  const totalDebtUsd = baseDebt * basePyth + quoteDebt * quotePyth
  const netEquityUsd = totalAssetsUsd - totalDebtUsd

  console.log(`Assets: ${fmt(baseAsset)} SUI + ${fmt(quoteAsset, 2)} USDC = ${fmtUsd(totalAssetsUsd)}`)
  console.log(`Debts:  ${fmt(baseDebt)} SUI + ${fmt(quoteDebt, 2)} USDC = ${fmtUsd(totalDebtUsd)}`)
  console.log(`Equity: ${fmtUsd(netEquityUsd)}`)

  if (baseAsset <= 0 && quoteAsset <= 0) {
    console.log('\nNo assets to close.')
    return
  }

  // ── 2. Plan ──

  separator('Close Plan (2-step)')

  // Step 1: Sell enough SUI to cover debt + small buffer, then repay
  const suiNeededForDebt = quoteDebt > 0 ? (quoteDebt * 1.005) / midPrice : 0 // 0.5% buffer
  let sellQtyStep1 = Math.min(suiNeededForDebt, baseAsset)
  if (bookParams.lotSize > 0) {
    sellQtyStep1 = Math.floor(sellQtyStep1 / bookParams.lotSize) * bookParams.lotSize
  }

  // Step 2: Sell remaining SUI, withdraw all
  const remainingSui = baseAsset - sellQtyStep1
  let sellQtyStep2 = remainingSui
  if (bookParams.lotSize > 0) {
    sellQtyStep2 = Math.floor(sellQtyStep2 / bookParams.lotSize) * bookParams.lotSize
  }

  const estUsdcFromSell1 = sellQtyStep1 * midPrice
  const estUsdcFromSell2 = sellQtyStep2 * midPrice
  const takerFeePct = 0.0001
  const totalFee = (estUsdcFromSell1 + estUsdcFromSell2) * takerFeePct
  const estNetUsdc = estUsdcFromSell1 + estUsdcFromSell2 + quoteAsset - quoteDebt - totalFee

  console.log(`TX1: Sell ${fmt(sellQtyStep1, 2)} SUI → repay ${fmt(quoteDebt, 2)} USDC debt`)
  console.log(`TX2: Sell ${fmt(sellQtyStep2, 2)} SUI → withdraw all USDC`)
  console.log(`Total taker fee (0.01%): ${fmtUsd(totalFee)}`)
  console.log(`Est. net withdrawal: ~${fmtUsd(estNetUsdc)} USDC`)

  // ══════════════════════════════════════════════════════════════════════════
  // TX1: Sell SUI to cover debt → repay
  // ══════════════════════════════════════════════════════════════════════════

  separator('TX1: Sell + Repay Debt')

  const tx1 = new Transaction()

  if (sellQtyStep1 > 0) {
    tx1.add(client.poolProxy.placeMarketOrder({
      poolKey: TARGET_POOL_KEY,
      marginManagerKey: 'MM_0',
      clientOrderId: Date.now().toString(),
      quantity: sellQtyStep1,
      isBid: false,
      payWithDeep: false,
    }))
    console.log(`Market SELL ${fmt(sellQtyStep1, 2)} SUI`)
  }

  if (quoteDebt > 0) {
    tx1.add(client.marginManager.repayQuote('MM_0'))
    console.log(`Repay all USDC debt`)
  }
  if (baseDebt > 0) {
    tx1.add(client.marginManager.repayBase('MM_0'))
    console.log(`Repay all SUI debt`)
  }

  console.log('\nDry-run TX1...')
  const dry1 = await buildAndDryRun(tx1, suiClient, address)
  console.log(`TX1 dry-run: ${dry1.success ? 'SUCCESS' : 'FAILED'} (gas: ${dry1.gasUsed.toFixed(6)} SUI)`)
  if (!dry1.success) {
    console.error(`TX1 error: ${dry1.error}`)
    process.exit(1)
  }

  if (dryRun) {
    // Also dry-run TX2
    separator('TX2: Sell Remaining + Withdraw (dry-run)')

    const tx2 = new Transaction()
    if (sellQtyStep2 > 0) {
      tx2.add(client.poolProxy.placeMarketOrder({
        poolKey: TARGET_POOL_KEY,
        marginManagerKey: 'MM_0',
        clientOrderId: (Date.now() + 1).toString(),
        quantity: sellQtyStep2,
        isBid: false,
        payWithDeep: false,
      }))
      console.log(`Market SELL ${fmt(sellQtyStep2, 2)} SUI`)
    }

    // After TX1 debt is repaid, estimate available USDC
    const estAvailableUsdc = (estUsdcFromSell1 - quoteDebt) + quoteAsset + estUsdcFromSell2
    const withdrawUsdc = Math.max(0, estAvailableUsdc * 0.99)
    if (withdrawUsdc > 0.01) {
      const coin = tx2.add(client.marginManager.withdrawQuote('MM_0', withdrawUsdc))
      tx2.transferObjects([coin], address)
      console.log(`Withdraw ~${fmt(withdrawUsdc, 2)} USDC`)
    }

    console.log('\n(TX2 cannot be dry-run without TX1 executing first)')

    separator('DRY-RUN COMPLETE')
    console.log('Both TXs planned. Run without --dry-run to execute.')
    return
  }

  // ── Execute TX1 ──
  console.log('\nExecuting TX1...')
  const result1 = await executeTransaction(tx1, suiClient, keypair)
  console.log(`TX1 Digest: ${result1.digest}`)
  console.log(`TX1 Status: ${result1.effects?.status?.status}`)

  if (result1.effects?.status?.status !== 'success') {
    console.error(`TX1 failed: ${result1.effects?.status?.error}`)
    process.exit(1)
  }

  // Show balance changes
  if (result1.balanceChanges?.length) {
    for (const bc of result1.balanceChanges) {
      const coinType = bc.coinType.split('::').pop() ?? bc.coinType
      const amount = Number(bc.amount)
      const decimals = coinType === 'SUI' ? SUI_DECIMALS : USDC_DECIMALS
      const humanAmount = amount / (10 ** decimals)
      console.log(`  ${coinType}: ${humanAmount >= 0 ? '+' : ''}${fmt(humanAmount, decimals === 9 ? 4 : 2)}`)
    }
  }

  // Wait for state to settle
  console.log('\nWaiting for state settlement...')
  await new Promise(r => setTimeout(r, 3000))

  // Verify debt cleared
  let postState1: any
  try {
    postState1 = await client.getMarginManagerState('MM_0')
    const postQuoteDebt = parseFloat(postState1.quoteDebt)
    const postBaseAsset = parseFloat(postState1.baseAsset)
    const postQuoteAsset = parseFloat(postState1.quoteAsset)
    console.log(`Post-TX1: ${fmt(postBaseAsset)} SUI + ${fmt(postQuoteAsset, 2)} USDC | Debt: ${fmt(postQuoteDebt, 2)} USDC`)

    if (postQuoteDebt > 0.01) {
      console.error(`Debt not fully repaid (${fmt(postQuoteDebt, 2)} USDC remaining). Aborting TX2.`)
      console.error('Manual intervention may be needed.')
      process.exit(1)
    }
  } catch (e: any) {
    console.error(`Could not verify state: ${e.message}`)
    process.exit(1)
  }

  // ══════════════════════════════════════════════════════════════════════════
  // TX2: Sell remaining SUI → withdraw all
  // ══════════════════════════════════════════════════════════════════════════

  separator('TX2: Sell Remaining + Withdraw')

  const postBaseAsset2 = parseFloat(postState1.baseAsset)
  const postQuoteAsset2 = parseFloat(postState1.quoteAsset)

  // Re-calculate sell qty from actual post-TX1 state
  let actualSellQty2 = postBaseAsset2
  if (bookParams.lotSize > 0) {
    actualSellQty2 = Math.floor(actualSellQty2 / bookParams.lotSize) * bookParams.lotSize
  }
  const suiDust2 = postBaseAsset2 - actualSellQty2

  const tx2 = new Transaction()

  if (actualSellQty2 > bookParams.minSize) {
    tx2.add(client.poolProxy.placeMarketOrder({
      poolKey: TARGET_POOL_KEY,
      marginManagerKey: 'MM_0',
      clientOrderId: Date.now().toString(),
      quantity: actualSellQty2,
      isBid: false,
      payWithDeep: false,
    }))
    console.log(`Market SELL ${fmt(actualSellQty2, 2)} SUI`)
  }

  // Settle + withdraw USDC
  tx2.add(client.poolProxy.withdrawSettledAmounts('MM_0'))

  const midPrice2 = await client.midPrice(TARGET_POOL_KEY)
  const estUsdc2 = postQuoteAsset2 + actualSellQty2 * midPrice2 * 0.995 // conservative
  if (estUsdc2 > 0.01) {
    const coin = tx2.add(client.marginManager.withdrawQuote('MM_0', estUsdc2))
    tx2.transferObjects([coin], address)
    console.log(`Withdraw ~${fmt(estUsdc2, 2)} USDC`)
  }

  // Withdraw SUI dust if any
  if (suiDust2 > 0.01) {
    const coin = tx2.add(client.marginManager.withdrawBase('MM_0', suiDust2))
    tx2.transferObjects([coin], address)
    console.log(`Withdraw ${fmt(suiDust2)} SUI dust`)
  }

  console.log('\nDry-run TX2...')
  const dry2 = await buildAndDryRun(tx2, suiClient, address)
  console.log(`TX2 dry-run: ${dry2.success ? 'SUCCESS' : 'FAILED'} (gas: ${dry2.gasUsed.toFixed(6)} SUI)`)
  if (!dry2.success) {
    console.error(`TX2 error: ${dry2.error}`)
    console.error('Try selling remaining via DeepBook UI, or re-run this script.')
    process.exit(1)
  }

  console.log('\nExecuting TX2...')
  const result2 = await executeTransaction(tx2, suiClient, keypair)
  console.log(`TX2 Digest: ${result2.digest}`)
  console.log(`TX2 Status: ${result2.effects?.status?.status}`)

  if (result2.effects?.status?.status !== 'success') {
    console.error(`TX2 failed: ${result2.effects?.status?.error}`)
    process.exit(1)
  }

  if (result2.balanceChanges?.length) {
    for (const bc of result2.balanceChanges) {
      const coinType = bc.coinType.split('::').pop() ?? bc.coinType
      const amount = Number(bc.amount)
      const decimals = coinType === 'SUI' ? SUI_DECIMALS : USDC_DECIMALS
      const humanAmount = amount / (10 ** decimals)
      console.log(`  ${coinType}: ${humanAmount >= 0 ? '+' : ''}${fmt(humanAmount, decimals === 9 ? 4 : 2)}`)
    }
  }

  // ── Final State ──

  separator('Final State')

  await new Promise(r => setTimeout(r, 3000))

  try {
    const finalState = await client.getMarginManagerState('MM_0')
    const fb = parseFloat(finalState.baseAsset)
    const fq = parseFloat(finalState.quoteAsset)
    const fbd = parseFloat(finalState.baseDebt)
    const fqd = parseFloat(finalState.quoteDebt)
    console.log(`Assets: ${fmt(fb)} SUI + ${fmt(fq, 2)} USDC`)
    console.log(`Debts:  ${fmt(fbd)} SUI + ${fmt(fqd, 2)} USDC`)
    if (fb < 0.01 && fq < 0.01 && fbd < 0.01 && fqd < 0.01) {
      console.log('✓ Position fully closed')
    } else {
      console.log('⚠ Residual balance remains')
    }
  } catch (e: any) {
    console.log(`State check: ${e.message}`)
  }

  separator('Wallet Balance')
  const [suiBal, usdcBal] = await Promise.all([
    suiClient.getBalance({ owner: address }),
    suiClient.getBalance({ owner: address, coinType: USDC_COIN_TYPE }),
  ])
  console.log(`SUI:  ${fmt(Number(suiBal.totalBalance) / 1e9)}`)
  console.log(`USDC: ${fmt(Number(usdcBal.totalBalance) / 1e6, 2)}`)

  separator('Done')
}

main().catch(err => {
  console.error('Fatal error:', err)
  process.exit(1)
})
