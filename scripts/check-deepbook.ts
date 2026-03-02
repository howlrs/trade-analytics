/**
 * check-deepbook.ts
 *
 * DeepBook Margin ポジション確認スクリプト
 * - MarginManager 自動検出（shared object → トランザクション履歴から検索）
 * - マージンポジション状態（資産、負債、ヘルスファクター）
 * - オープン注文・条件付き注文
 * - プール情報（mid price, オーダーブック深度, リスクパラメータ）
 *
 * Usage: npx tsx scripts/check-deepbook.ts
 *
 * 環境変数:
 *   DEEPBOOK_MARGIN_MANAGER_IDS  (任意) カンマ区切りのMarginManager ID
 *     設定しない場合、直近のトランザクション履歴から自動検出
 */

import 'dotenv/config'
import { DeepBookClient, mainnetPackageIds, testnetPackageIds } from '@mysten/deepbook-v3'
import { getFullnodeUrl, SuiClient } from '@mysten/sui/client'
import { initLogger, getLogger } from '../src/utils/logger.js'
import { loadKeypair } from '../src/utils/wallet.js'

// ─── Patch outdated SDK constants ────────────────────────────────────────────
// SDK v0.28.3 has stale mainnet MARGIN_PACKAGE_ID / LIQUIDATION_PACKAGE_ID.
// Ref: https://github.com/MystenLabs/ts-sdks/blob/main/packages/deepbook-v3/src/utils/constants.ts
;(mainnetPackageIds as any).MARGIN_PACKAGE_ID = '0xfbd322126f1452fd4c89aedbaeb9fd0c44df9b5cedbe70d76bf80dc086031377'
;(mainnetPackageIds as any).LIQUIDATION_PACKAGE_ID = '0x55718c06706bee34c9f3c39f662f10be354a4dcc719699ad72091dc343b641b8'

// ─── Constants ───────────────────────────────────────────────────────────────

const FLOAT_SCALAR = 1_000_000 // DeepBook price scalar (1e6)
const TARGET_POOL_KEY = 'SUI_USDC'

// ─── MarginManager Discovery ────────────────────────────────────────────────
// MarginManager is a SHARED object (not owned), so getOwnedObjects won't find it.
// We scan the user's recent transactions for `margin_manager::MarginManager` creation events.

async function discoverMarginManagersFromTxHistory(
  suiClient: SuiClient,
  address: string,
  env: 'mainnet' | 'testnet',
): Promise<string[]> {
  const pkgIds = env === 'mainnet' ? mainnetPackageIds : testnetPackageIds
  const marginManagerType = `${pkgIds.MARGIN_PACKAGE_ID}::margin_manager::MarginManager`

  const ids: string[] = []
  let cursor: string | null | undefined = undefined
  let hasNext = true
  let pages = 0
  const MAX_PAGES = 10 // Scan up to 500 txns

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
            if (objId && !ids.includes(objId)) {
              ids.push(objId)
            }
          }
        }
      }
    }

    hasNext = resp.hasNextPage
    cursor = resp.nextCursor
    pages++
  }

  // Verify each is still a live shared object
  const liveIds: string[] = []
  for (const id of ids) {
    try {
      const obj = await suiClient.getObject({ id, options: { showType: true, showOwner: true } })
      if (obj.data) {
        liveIds.push(id)
      }
    } catch { /* deleted or wrapped */ }
  }

  return liveIds
}

async function discoverBalanceManagers(
  suiClient: SuiClient,
  owner: string,
  env: 'mainnet' | 'testnet',
): Promise<string[]> {
  const pkgIds = env === 'mainnet' ? mainnetPackageIds : testnetPackageIds
  const bmType = `${pkgIds.DEEPBOOK_PACKAGE_ID}::balance_manager::BalanceManager`
  const ids: string[] = []
  let cursor: string | null | undefined = undefined
  let hasNext = true
  while (hasNext) {
    const resp = await suiClient.getOwnedObjects({
      owner,
      filter: { StructType: bmType },
      options: { showType: true },
      cursor: cursor ?? undefined,
    })
    for (const item of resp.data) {
      if (item.data?.objectId) ids.push(item.data.objectId)
    }
    hasNext = resp.hasNextPage
    cursor = resp.nextCursor
  }
  return ids
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

  console.log(`DeepBook Margin Position Check`)
  console.log(`Network: ${network} | Address: ${address}`)

  // ── 1. Discover MarginManagers ──

  separator('Object Discovery')

  // Check env var first, then scan tx history
  let marginManagerIds: string[] = []
  const envMmIds = process.env.DEEPBOOK_MARGIN_MANAGER_IDS
  if (envMmIds) {
    marginManagerIds = envMmIds.split(',').map(s => s.trim()).filter(Boolean)
    console.log(`MarginManagers (from env): ${marginManagerIds.length}`)
  } else {
    console.log('Scanning transaction history for MarginManagers...')
    marginManagerIds = await discoverMarginManagersFromTxHistory(suiClient, address, network)
    console.log(`MarginManagers found: ${marginManagerIds.length}`)
  }

  for (const id of marginManagerIds) {
    console.log(`  ${id}`)
  }

  const balanceManagerIds = await discoverBalanceManagers(suiClient, address, network)
  if (balanceManagerIds.length > 0) {
    console.log(`BalanceManagers (owned): ${balanceManagerIds.length}`)
    for (const id of balanceManagerIds) {
      console.log(`  ${id}`)
    }
  }

  if (marginManagerIds.length === 0 && balanceManagerIds.length === 0) {
    console.log('\nNo DeepBook MarginManager or BalanceManager found.')
    console.log('Tips:')
    console.log('  - Set DEEPBOOK_MARGIN_MANAGER_IDS in .env if you know the ID')
    console.log('  - Ensure SUI_PRIVATE_KEY matches the wallet that created the position')
  }

  // ── 2. Pool Info ──

  separator('Pool: SUI_USDC')

  const discoveryClient = new DeepBookClient({
    client: suiClient,
    address,
    env: network,
  })

  const midPrice = await discoveryClient.midPrice(TARGET_POOL_KEY)
  console.log(`Mid Price (SUI/USDC): ${fmt(midPrice)}`)

  const tradeParams = await discoveryClient.poolTradeParams(TARGET_POOL_KEY)
  console.log(`Taker Fee: ${(tradeParams.takerFee * 100).toFixed(4)}% | Maker Fee: ${(tradeParams.makerFee * 100).toFixed(4)}%`)

  // Order book depth (5 ticks from mid)
  try {
    const depth = await discoveryClient.getLevel2TicksFromMid(TARGET_POOL_KEY, 5)
    console.log(`\nOrder Book (5 ticks from mid):`)
    console.log(`  Asks: ${depth.ask_prices.map((p, i) => `${fmt(p)}×${fmt(depth.ask_quantities[i], 0)}`).join(' | ')}`)
    console.log(`  Bids: ${depth.bid_prices.map((p, i) => `${fmt(p)}×${fmt(depth.bid_quantities[i], 0)}`).join(' | ')}`)
  } catch {
    log.debug('Could not fetch order book depth')
  }

  // Note: getLiquidationRiskRatio/getMinBorrowRiskRatio crash in SDK 0.28.x (devInspect bug)
  // Typical DeepBook Margin thresholds: Liquidation ~1.125x, Borrow ~1.25x, Withdraw ~2.0x

  // Margin pool interest rates
  try {
    const [suiRate, usdcRate] = await Promise.all([
      discoveryClient.getMarginPoolInterestRate('SUI'),
      discoveryClient.getMarginPoolInterestRate('USDC'),
    ])
    const [suiSupply, usdcSupply, suiBorrow, usdcBorrow] = await Promise.all([
      discoveryClient.getMarginPoolTotalSupply('SUI'),
      discoveryClient.getMarginPoolTotalSupply('USDC'),
      discoveryClient.getMarginPoolTotalBorrow('SUI'),
      discoveryClient.getMarginPoolTotalBorrow('USDC'),
    ])
    console.log(`\nMargin Pool Stats:`)
    console.log(`  SUI  - Rate: ${(suiRate * 100).toFixed(2)}% | Supply: ${fmt(suiSupply, 0)} | Borrow: ${fmt(suiBorrow, 0)}`)
    console.log(`  USDC - Rate: ${(usdcRate * 100).toFixed(2)}% | Supply: ${fmt(usdcSupply, 0)} | Borrow: ${fmt(usdcBorrow, 0)}`)
  } catch (e: any) {
    log.debug('Could not fetch margin pool stats', { error: e.message })
  }

  // ── 3. MarginManager Details ──

  if (marginManagerIds.length > 0) {
    const marginManagers: Record<string, { address: string; poolKey: string }> = {}
    for (let i = 0; i < marginManagerIds.length; i++) {
      marginManagers[`MM_${i}`] = {
        address: marginManagerIds[i],
        poolKey: TARGET_POOL_KEY,
      }
    }

    const client = new DeepBookClient({
      client: suiClient,
      address,
      env: network,
      marginManagers,
    })

    for (let i = 0; i < marginManagerIds.length; i++) {
      const key = `MM_${i}`
      const mmId = marginManagerIds[i]

      separator(`Margin Position #${i}`)
      console.log(`MarginManager: ${mmId}`)

      // Full state
      try {
        const state = await client.getMarginManagerState(key)

        const baseAsset = parseFloat(state.baseAsset)
        const quoteAsset = parseFloat(state.quoteAsset)
        const baseDebt = parseFloat(state.baseDebt)
        const quoteDebt = parseFloat(state.quoteDebt)

        // Pyth oracle prices
        const basePythPrice = parseFloat(state.basePythPrice) / (10 ** state.basePythDecimals)
        const quotePythPrice = parseFloat(state.quotePythPrice) / (10 ** state.quotePythDecimals)

        const totalAssetsUsd = baseAsset * basePythPrice + quoteAsset * quotePythPrice
        const totalDebtUsd = baseDebt * basePythPrice + quoteDebt * quotePythPrice
        const netEquityUsd = totalAssetsUsd - totalDebtUsd

        // Position direction
        let direction = 'NEUTRAL'
        if (baseDebt > 0 && quoteDebt === 0) direction = 'SHORT SUI'
        else if (quoteDebt > 0 && baseDebt === 0) direction = 'LONG SUI'
        else if (baseDebt > 0 && quoteDebt > 0) direction = 'MIXED'

        const leverage = netEquityUsd > 0 ? totalAssetsUsd / netEquityUsd : 0

        console.log()
        console.log(`  Direction:   ${direction}`)
        console.log(`  Leverage:    ${leverage > 0 ? fmt(leverage, 2) + 'x' : 'N/A'}`)
        // Note: getLiquidationRiskRatio() crashes in SDK 0.28.x devInspect, use docs default
        const LIQ_RATIO_DEFAULT = 1.125 // DeepBook docs: typical liquidation threshold
        console.log(`  Risk Ratio:  ${fmt(state.riskRatio, 4)} (liquidation ~${fmt(LIQ_RATIO_DEFAULT, 3)}x)`)
        console.log()

        console.log(`  Assets:`)
        console.log(`    SUI:   ${fmt(baseAsset)} (${fmtUsd(baseAsset * basePythPrice)})`)
        console.log(`    USDC:  ${fmt(quoteAsset)} (${fmtUsd(quoteAsset * quotePythPrice)})`)
        console.log(`    Total: ${fmtUsd(totalAssetsUsd)}`)
        console.log()

        console.log(`  Debts:`)
        console.log(`    SUI:   ${fmt(baseDebt)} (${fmtUsd(baseDebt * basePythPrice)})`)
        console.log(`    USDC:  ${fmt(quoteDebt)} (${fmtUsd(quoteDebt * quotePythPrice)})`)
        console.log(`    Total: ${fmtUsd(totalDebtUsd)}`)
        console.log()

        console.log(`  Net Equity: ${fmtUsd(netEquityUsd)}`)
        console.log()

        console.log(`  Oracle (Pyth): SUI=${fmtUsd(basePythPrice)} USDC=${fmtUsd(quotePythPrice)}`)

        // Trigger prices
        const currentPrice = Number(state.currentPrice) / FLOAT_SCALAR
        console.log(`  DeepBook Price: ${fmt(currentPrice)}`)

        const lowestAbove = Number(state.lowestTriggerAbovePrice)
        const highestBelow = Number(state.highestTriggerBelowPrice)
        const MAX_U64 = 2n ** 64n - 1n
        if (BigInt(lowestAbove) < MAX_U64) {
          console.log(`  Trigger Above: ${fmt(lowestAbove / FLOAT_SCALAR)}`)
        }
        if (highestBelow > 0) {
          console.log(`  Trigger Below: ${fmt(highestBelow / FLOAT_SCALAR)}`)
        }
      } catch (e: any) {
        console.log(`  Error fetching state: ${e.message}`)
        log.debug('MarginManager state error', { mmId, error: e.message })
      }

      // Open orders
      try {
        const orders = await client.getMarginAccountOrderDetails(key)
        if (orders.length > 0) {
          console.log()
          console.log(`  Open Orders (${orders.length}):`)
          for (const order of orders) {
            const decoded = client.decodeOrderId(BigInt(order.order_id))
            const qty = parseFloat(order.quantity)
            const filled = parseFloat(order.filled_quantity)
            const fillPct = qty > 0 ? ((filled / qty) * 100).toFixed(1) : '0.0'
            console.log(`    ${decoded.isBid ? 'BID' : 'ASK'} @ ${fmt(decoded.price)} | Qty: ${fmt(qty, 2)} | Filled: ${fillPct}%`)
          }
        } else {
          console.log(`\n  Open Orders: none`)
        }
      } catch (e: any) {
        log.debug('Could not fetch orders', { error: e.message })
      }

      // Conditional orders (TP/SL)
      try {
        const conditionalIds = await client.getConditionalOrderIds(key)
        if (conditionalIds.length > 0) {
          console.log(`  Conditional Orders (TP/SL): ${conditionalIds.length}`)
          for (const id of conditionalIds) {
            console.log(`    ${id}`)
          }
        }
      } catch (e: any) {
        log.debug('Could not fetch conditional orders', { error: e.message })
      }
    }
  }

  // ── 4. BalanceManager Details (standalone, non-margin) ──

  const coveredBmIds = new Set<string>()
  if (marginManagerIds.length > 0) {
    const tmpManagers: Record<string, { address: string; poolKey: string }> = {}
    for (let i = 0; i < marginManagerIds.length; i++) {
      tmpManagers[`MM_${i}`] = { address: marginManagerIds[i], poolKey: TARGET_POOL_KEY }
    }
    const tmpClient = new DeepBookClient({
      client: suiClient, address, env: network, marginManagers: tmpManagers,
    })
    for (let i = 0; i < marginManagerIds.length; i++) {
      try {
        const bmId = await tmpClient.getMarginManagerBalanceManagerId(`MM_${i}`)
        coveredBmIds.add(bmId)
      } catch { /* ignore */ }
    }
  }

  const standaloneBmIds = balanceManagerIds.filter(id => !coveredBmIds.has(id))
  if (standaloneBmIds.length > 0) {
    const bmMap: Record<string, { address: string }> = {}
    for (let i = 0; i < standaloneBmIds.length; i++) {
      bmMap[`BM_${i}`] = { address: standaloneBmIds[i] }
    }
    const bmClient = new DeepBookClient({
      client: suiClient, address, env: network, balanceManagers: bmMap,
    })

    for (let i = 0; i < standaloneBmIds.length; i++) {
      const key = `BM_${i}`
      const bmId = standaloneBmIds[i]
      separator(`BalanceManager #${i}: ${bmId.slice(0, 10)}...${bmId.slice(-6)}`)

      for (const coinKey of ['SUI', 'USDC', 'DEEP']) {
        try {
          const bal = await bmClient.checkManagerBalance(key, coinKey)
          if (bal.balance > 0) {
            console.log(`  ${coinKey}: ${fmt(bal.balance)}`)
          }
        } catch { /* coin might not exist */ }
      }

      try {
        const orders = await bmClient.getAccountOrderDetails(TARGET_POOL_KEY, key)
        if (orders.length > 0) {
          console.log(`  Open Orders (${orders.length}):`)
          for (const order of orders) {
            const decoded = bmClient.decodeOrderId(BigInt(order.order_id))
            console.log(`    ${decoded.isBid ? 'BID' : 'ASK'} @ ${fmt(decoded.price)} | Qty: ${fmt(parseFloat(order.quantity), 2)}`)
          }
        }
      } catch { /* no account in pool */ }
    }
  }

  // ── 5. Indexer: Recent Trades ──

  if (network === 'mainnet') {
    separator('Recent Trades (SUI_USDC)')
    try {
      const resp = await fetch('https://deepbook-indexer.mainnet.mystenlabs.com/trades/SUI_USDC')
      if (resp.ok) {
        const trades = (await resp.json()) as any[]
        for (const t of trades.slice(0, 5)) {
          const side = t.taker_is_bid ? 'BUY ' : 'SELL'
          const vol = t.base_volume ?? 0
          console.log(`  ${side} ${fmt(vol, 1)} SUI @ ${fmt(t.price)} (${fmtUsd(t.quote_volume ?? 0)}) | ${new Date(t.timestamp).toLocaleTimeString()}`)
        }
      }
    } catch {
      console.log('  Could not fetch indexer data')
    }
  }

  separator('Done')
}

main().catch(err => {
  console.error('Fatal error:', err)
  process.exit(1)
})
