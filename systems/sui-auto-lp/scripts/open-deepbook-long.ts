/**
 * open-deepbook-long.ts
 *
 * DeepBook Margin で SUI ロングポジションを建てるスクリプト。
 * 既存の MarginManager にUSDCをデポジットし、成行 or 指値で SUI を購入する。
 *
 * Usage:
 *   npx tsx scripts/open-deepbook-long.ts --usdc 10 --leverage 2 --dry-run
 *   npx tsx scripts/open-deepbook-long.ts --usdc 10 --leverage 2
 *   npx tsx scripts/open-deepbook-long.ts --usdc 10 --leverage 2 --price 3.50
 *
 * 環境変数:
 *   SUI_PRIVATE_KEY               (必須) ウォレット秘密鍵
 *   SUI_NETWORK                   (任意) mainnet | testnet (default: mainnet)
 *   DEEPBOOK_MARGIN_MANAGER_IDS   (任意) MarginManager ID (未設定時はTX履歴から自動検出)
 */

import 'dotenv/config'
import { DeepBookClient, mainnetPackageIds, testnetPackageIds, OrderType } from '@mysten/deepbook-v3'
import { getFullnodeUrl, SuiClient } from '@mysten/sui/client'
import { Transaction } from '@mysten/sui/transactions'
import { initLogger, getLogger } from '../src/utils/logger.js'
import { loadKeypair } from '../src/utils/wallet.js'

// ─── Patch outdated SDK constants ────────────────────────────────────────────
// SDK v0.28.3 has stale mainnet MARGIN_PACKAGE_ID / LIQUIDATION_PACKAGE_ID.
// Patch before any DeepBookClient construction.
// Ref: https://github.com/MystenLabs/ts-sdks/blob/main/packages/deepbook-v3/src/utils/constants.ts
;(mainnetPackageIds as any).MARGIN_PACKAGE_ID = '0xfbd322126f1452fd4c89aedbaeb9fd0c44df9b5cedbe70d76bf80dc086031377'
;(mainnetPackageIds as any).LIQUIDATION_PACKAGE_ID = '0x55718c06706bee34c9f3c39f662f10be354a4dcc719699ad72091dc343b641b8'

// ─── Constants ───────────────────────────────────────────────────────────────

const FLOAT_SCALAR = 1_000_000
const TARGET_POOL_KEY = 'SUI_USDC'
const USDC_DECIMALS = 6
const USDC_COIN_TYPE = '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC'

// ─── CLI Args ────────────────────────────────────────────────────────────────

interface CliArgs {
  usdc: number
  leverage: number
  price?: number
  dryRun: boolean
}

function parseArgs(): CliArgs {
  const args = process.argv.slice(2)
  let usdc: number | undefined
  let leverage: number | undefined
  let price: number | undefined
  let dryRun = false

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--usdc':
        usdc = parseFloat(args[++i])
        break
      case '--leverage':
        leverage = parseFloat(args[++i])
        break
      case '--price':
        price = parseFloat(args[++i])
        break
      case '--dry-run':
        dryRun = true
        break
    }
  }

  if (usdc === undefined || isNaN(usdc) || usdc <= 0) {
    console.error('Error: --usdc <amount> is required (positive number)')
    process.exit(1)
  }
  if (leverage === undefined || isNaN(leverage) || leverage < 1) {
    console.error('Error: --leverage <multiplier> is required (>= 1)')
    process.exit(1)
  }
  if (price !== undefined && (isNaN(price) || price <= 0)) {
    console.error('Error: --price must be a positive number')
    process.exit(1)
  }

  return { usdc, leverage, price, dryRun }
}

// ─── MarginManager Discovery (same as check-deepbook.ts) ────────────────────

// On Sui, struct types use the ORIGINAL package ID (v1), not upgraded versions.
// The SDK's MARGIN_PACKAGE_ID may point to v2/v3, so we hardcode v1 for type matching.
const MARGIN_TYPE_ORIGINS: Record<string, string> = {
  mainnet: '0x97d9473771b01f77b0940c589484184b49f6444627ec121314fae6a6d36fb86b',
  testnet: '0xd6a42f4df4db73d68cbeb52be66698d2fe6a9464f45ad113ca52b0c6ebd918b6',
}

async function discoverMarginManagers(
  suiClient: SuiClient,
  address: string,
  env: 'mainnet' | 'testnet',
): Promise<string[]> {
  const originPkg = MARGIN_TYPE_ORIGINS[env]
  const marginManagerType = `${originPkg}::margin_manager::MarginManager`

  const ids: string[] = []
  let cursor: string | null | undefined = undefined
  let hasNext = true
  let pages = 0
  const MAX_PAGES = 10

  while (hasNext && pages < MAX_PAGES) {
    const resp = await suiClient.queryTransactionBlocks({
      filter: { FromAddress: address },
      options: { showObjectChanges: true },
      limit: 50,
      order: 'descending',
      cursor: cursor ?? undefined,
    })

    for (const tx of resp.data) {
      for (const change of (tx.objectChanges ?? [])) {
        if (change.type === 'created') {
          const objType = (change as any).objectType ?? ''
          if (objType.startsWith(marginManagerType)) {
            const objId = (change as any).objectId
            if (objId && !ids.includes(objId)) ids.push(objId)
          }
        }
      }
    }

    hasNext = resp.hasNextPage
    cursor = resp.nextCursor
    pages++
  }

  // Verify live objects
  const liveIds: string[] = []
  for (const id of ids) {
    try {
      const obj = await suiClient.getObject({ id, options: { showType: true, showOwner: true } })
      if (obj.data) liveIds.push(id)
    } catch { /* deleted or wrapped */ }
  }

  return liveIds
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmt(val: string | number, decimals = 4): string {
  const n = typeof val === 'string' ? parseFloat(val) : val
  if (isNaN(n)) return String(val)
  return n.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function fmtUsd(val: number): string {
  return `$${fmt(val, 2)}`
}

function separator(title: string) {
  console.log()
  console.log(`━━━ ${title} ${'━'.repeat(Math.max(0, 60 - title.length))}`)
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function main() {
  const cliArgs = parseArgs()
  initLogger(process.env.LOG_LEVEL ?? 'warn')
  const log = getLogger()

  const privateKey = process.env.SUI_PRIVATE_KEY
  if (!privateKey) {
    console.error('SUI_PRIVATE_KEY not set in .env')
    process.exit(1)
  }

  const network = (process.env.SUI_NETWORK ?? 'mainnet') as 'mainnet' | 'testnet'
  const keypair = loadKeypair(privateKey)
  const address = keypair.getPublicKey().toSuiAddress()
  const suiClient = new SuiClient({ url: getFullnodeUrl(network) })

  console.log(`DeepBook Margin Long`)
  console.log(`Network: ${network} | Address: ${address}`)
  console.log(`Mode: ${cliArgs.dryRun ? 'DRY-RUN' : 'LIVE EXECUTION'}`)

  // ── 1. Discover MarginManager ──

  separator('MarginManager Discovery')

  let marginManagerIds: string[] = []
  const envMmIds = process.env.DEEPBOOK_MARGIN_MANAGER_IDS
  if (envMmIds) {
    marginManagerIds = envMmIds.split(',').map(s => s.trim()).filter(Boolean)
    console.log(`MarginManagers (from env): ${marginManagerIds.length}`)
  } else {
    console.log('Scanning transaction history for MarginManagers...')
    marginManagerIds = await discoverMarginManagers(suiClient, address, network)
    console.log(`MarginManagers found: ${marginManagerIds.length}`)
  }

  if (marginManagerIds.length === 0) {
    console.error('\nNo MarginManager found. Create one via DeepBook UI first.')
    console.error('Or set DEEPBOOK_MARGIN_MANAGER_IDS in .env')
    process.exit(1)
  }

  // Use first MarginManager
  const mmId = marginManagerIds[0]
  console.log(`Using: ${mmId}`)

  // ── 2. Initialize DeepBookClient ──

  const client = new DeepBookClient({
    client: suiClient,
    address,
    env: network,
    marginManagers: {
      MM_0: { address: mmId, poolKey: TARGET_POOL_KEY },
    },
  })

  // ── 3. Current State ──

  separator('Current State')

  // Mid price
  const midPrice = await client.midPrice(TARGET_POOL_KEY)
  console.log(`Mid Price (SUI/USDC): ${fmt(midPrice)}`)

  // Pool book params (lot size, min size)
  const bookParams = await client.poolBookParams(TARGET_POOL_KEY)
  console.log(`Lot Size: ${bookParams.lotSize} | Min Size: ${bookParams.minSize} | Tick Size: ${bookParams.tickSize}`)

  // Wallet USDC balance
  const usdcBalance = await suiClient.getBalance({ owner: address, coinType: USDC_COIN_TYPE })
  const walletUsdc = Number(usdcBalance.totalBalance) / 10 ** USDC_DECIMALS
  console.log(`Wallet USDC: ${fmt(walletUsdc, 2)}`)

  if (walletUsdc < cliArgs.usdc) {
    console.error(`\nInsufficient USDC: wallet has ${fmt(walletUsdc, 2)} but need ${fmt(cliArgs.usdc, 2)}`)
    process.exit(1)
  }

  // Existing margin position
  try {
    const state = await client.getMarginManagerState('MM_0')
    const baseAsset = parseFloat(state.baseAsset)
    const quoteAsset = parseFloat(state.quoteAsset)
    const baseDebt = parseFloat(state.baseDebt)
    const quoteDebt = parseFloat(state.quoteDebt)
    const basePythPrice = parseFloat(state.basePythPrice) / (10 ** state.basePythDecimals)
    const quotePythPrice = parseFloat(state.quotePythPrice) / (10 ** state.quotePythDecimals)
    const totalAssetsUsd = baseAsset * basePythPrice + quoteAsset * quotePythPrice
    const totalDebtUsd = baseDebt * basePythPrice + quoteDebt * quotePythPrice
    const netEquityUsd = totalAssetsUsd - totalDebtUsd
    const currentLeverage = netEquityUsd > 0 ? totalAssetsUsd / netEquityUsd : 0

    let direction = 'NEUTRAL'
    if (baseDebt > 0 && quoteDebt === 0) direction = 'SHORT SUI'
    else if (quoteDebt > 0 && baseDebt === 0) direction = 'LONG SUI'
    else if (baseDebt > 0 && quoteDebt > 0) direction = 'MIXED'

    console.log()
    console.log(`Existing Position:`)
    console.log(`  Direction: ${direction} | Leverage: ${currentLeverage > 0 ? fmt(currentLeverage, 2) + 'x' : 'N/A'}`)
    console.log(`  Assets: ${fmt(baseAsset)} SUI + ${fmt(quoteAsset, 2)} USDC = ${fmtUsd(totalAssetsUsd)}`)
    console.log(`  Debts:  ${fmt(baseDebt)} SUI + ${fmt(quoteDebt, 2)} USDC = ${fmtUsd(totalDebtUsd)}`)
    console.log(`  Equity: ${fmtUsd(netEquityUsd)}`)
  } catch (e: any) {
    console.log(`\nNo existing position (or error: ${e.message})`)
  }

  // ── 4. Order Parameters ──

  separator('Order Parameters')

  const orderPrice = cliArgs.price ?? midPrice
  const isMarketOrder = cliArgs.price === undefined
  const totalBuyingPower = cliArgs.usdc * cliArgs.leverage
  let suiQuantity = totalBuyingPower / orderPrice

  // Round down to lot size
  if (bookParams.lotSize > 0) {
    suiQuantity = Math.floor(suiQuantity / bookParams.lotSize) * bookParams.lotSize
  }

  if (suiQuantity < bookParams.minSize) {
    console.error(`\nOrder quantity ${fmt(suiQuantity, 2)} SUI is below minimum ${bookParams.minSize}`)
    process.exit(1)
  }

  console.log(`Deposit:        ${fmt(cliArgs.usdc, 2)} USDC`)
  console.log(`Leverage:       ${cliArgs.leverage}x`)
  console.log(`Buying Power:   ${fmtUsd(totalBuyingPower)}`)
  console.log(`Order Type:     ${isMarketOrder ? 'MARKET' : `LIMIT POST_ONLY @ ${fmt(orderPrice)}`}`)
  console.log(`SUI Quantity:   ${fmt(suiQuantity, 2)} (${fmtUsd(suiQuantity * orderPrice)})`)

  // ── 5. Post-Trade Estimate ──

  separator('Post-Trade Estimate')

  try {
    const state = await client.getMarginManagerState('MM_0')
    const baseAsset = parseFloat(state.baseAsset)
    const quoteAsset = parseFloat(state.quoteAsset)
    const baseDebt = parseFloat(state.baseDebt)
    const quoteDebt = parseFloat(state.quoteDebt)
    const basePythPrice = parseFloat(state.basePythPrice) / (10 ** state.basePythDecimals)
    const quotePythPrice = parseFloat(state.quotePythPrice) / (10 ** state.quotePythDecimals)

    // After deposit + long: SUI increases, USDC debt increases (borrow for leverage)
    const newBaseAsset = baseAsset + suiQuantity
    const newQuoteAsset = quoteAsset + cliArgs.usdc // deposited USDC added
    const newQuoteDebt = quoteDebt + (totalBuyingPower - cliArgs.usdc) // borrowed amount

    const newTotalAssets = newBaseAsset * basePythPrice + newQuoteAsset * quotePythPrice
    const newTotalDebt = baseDebt * basePythPrice + newQuoteDebt * quotePythPrice
    const newEquity = newTotalAssets - newTotalDebt
    const newLeverage = newEquity > 0 ? newTotalAssets / newEquity : 0

    console.log(`Est. Assets: ${fmt(newBaseAsset)} SUI + ${fmt(newQuoteAsset, 2)} USDC = ${fmtUsd(newTotalAssets)}`)
    console.log(`Est. Debts:  ${fmt(baseDebt)} SUI + ${fmt(newQuoteDebt, 2)} USDC = ${fmtUsd(newTotalDebt)}`)
    console.log(`Est. Equity: ${fmtUsd(newEquity)}`)
    console.log(`Est. Leverage: ${fmt(newLeverage, 2)}x`)
  } catch {
    console.log(`(Could not estimate post-trade state)`)
  }

  // ── 6. Build Transaction ──

  separator('Transaction')

  const tx = new Transaction()

  // Step 1: depositQuote — deposit USDC collateral into margin manager
  tx.add(client.marginManager.depositQuote({
    managerKey: 'MM_0',
    amount: cliArgs.usdc,
  }))

  // Step 2: borrowQuote — borrow additional USDC from margin pool for leverage
  const borrowAmount = totalBuyingPower - cliArgs.usdc
  if (borrowAmount > 0) {
    tx.add(client.marginManager.borrowQuote('MM_0', borrowAmount))
    console.log(`Borrow ${fmt(borrowAmount, 2)} USDC from margin pool`)
  }

  // Step 3: Place order
  const clientOrderId = Date.now().toString()

  if (isMarketOrder) {
    tx.add(client.poolProxy.placeMarketOrder({
      poolKey: TARGET_POOL_KEY,
      marginManagerKey: 'MM_0',
      clientOrderId,
      quantity: suiQuantity,
      isBid: true,
      payWithDeep: false,
    }))
    console.log(`Market BID ${fmt(suiQuantity, 2)} SUI (order: ${clientOrderId})`)
  } else {
    tx.add(client.poolProxy.placeLimitOrder({
      poolKey: TARGET_POOL_KEY,
      marginManagerKey: 'MM_0',
      clientOrderId,
      price: cliArgs.price!,
      quantity: suiQuantity,
      isBid: true,
      payWithDeep: false,
      orderType: OrderType.POST_ONLY,
    }))
    console.log(`Limit BID (POST_ONLY) ${fmt(suiQuantity, 2)} SUI @ ${fmt(cliArgs.price!)} (order: ${clientOrderId})`)
  }

  // ── 7. Execute ──

  tx.setSender(address)
  const txBytes = await tx.build({ client: suiClient })

  // Always dry-run first
  console.log('\nRunning dry-run...')
  const dryResult = await suiClient.dryRunTransactionBlock({
    transactionBlock: Buffer.from(txBytes).toString('base64'),
  })

  const gasUsed = BigInt(dryResult.effects.gasUsed.computationCost) +
    BigInt(dryResult.effects.gasUsed.storageCost) -
    BigInt(dryResult.effects.gasUsed.storageRebate)
  const gasStatus = dryResult.effects.status.status

  console.log(`Dry-run: ${gasStatus} (gas: ${(Number(gasUsed) / 1e9).toFixed(6)} SUI)`)

  if (gasStatus !== 'success') {
    console.error(`\nDry-run FAILED: ${dryResult.effects.status.error}`)
    process.exit(1)
  }

  if (cliArgs.dryRun) {
    separator('DRY-RUN COMPLETE')
    console.log('Run without --dry-run to execute.')
    return
  }

  // Live execution
  console.log('\nExecuting transaction...')
  const result = await suiClient.signAndExecuteTransaction({
    transaction: tx,
    signer: keypair,
    options: { showEffects: true },
  })

  const txStatus = result.effects?.status?.status
  console.log(`TX Digest: ${result.digest}`)
  console.log(`Status: ${txStatus}`)

  if (txStatus !== 'success') {
    console.error(`Transaction failed: ${result.effects?.status?.error}`)
    process.exit(1)
  }

  // ── 8. Post-Trade State ──

  separator('Post-Trade State')

  // Wait for state to settle
  await new Promise(r => setTimeout(r, 3000))

  try {
    const state = await client.getMarginManagerState('MM_0')
    const baseAsset = parseFloat(state.baseAsset)
    const quoteAsset = parseFloat(state.quoteAsset)
    const baseDebt = parseFloat(state.baseDebt)
    const quoteDebt = parseFloat(state.quoteDebt)
    const basePythPrice = parseFloat(state.basePythPrice) / (10 ** state.basePythDecimals)
    const quotePythPrice = parseFloat(state.quotePythPrice) / (10 ** state.quotePythDecimals)
    const totalAssetsUsd = baseAsset * basePythPrice + quoteAsset * quotePythPrice
    const totalDebtUsd = baseDebt * basePythPrice + quoteDebt * quotePythPrice
    const netEquityUsd = totalAssetsUsd - totalDebtUsd
    const leverage = netEquityUsd > 0 ? totalAssetsUsd / netEquityUsd : 0

    let direction = 'NEUTRAL'
    if (baseDebt > 0 && quoteDebt === 0) direction = 'SHORT SUI'
    else if (quoteDebt > 0 && baseDebt === 0) direction = 'LONG SUI'
    else if (baseDebt > 0 && quoteDebt > 0) direction = 'MIXED'

    console.log(`Direction: ${direction} | Leverage: ${fmt(leverage, 2)}x`)
    console.log(`Assets: ${fmt(baseAsset)} SUI + ${fmt(quoteAsset, 2)} USDC = ${fmtUsd(totalAssetsUsd)}`)
    console.log(`Debts:  ${fmt(baseDebt)} SUI + ${fmt(quoteDebt, 2)} USDC = ${fmtUsd(totalDebtUsd)}`)
    console.log(`Equity: ${fmtUsd(netEquityUsd)}`)
    console.log(`Risk Ratio: ${fmt(parseFloat(state.riskRatio), 4)}`)
  } catch (e: any) {
    console.log(`Could not fetch post-trade state: ${e.message}`)
    console.log('Check with: npx tsx scripts/check-deepbook.ts')
  }

  separator('Done')
}

main().catch(err => {
  console.error('Fatal error:', err)
  process.exit(1)
})
