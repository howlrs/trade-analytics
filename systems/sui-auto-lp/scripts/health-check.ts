/**
 * Health Check / Audit Script
 *
 * ウォレット・ポジション・ボットの稼働状態を包括的に検証する。
 * 資金の整合性、ガス残高、ポジション状態、ログの異常を確認。
 *
 * Usage:
 *   npx tsx scripts/health-check.ts              # 基本チェック
 *   npx tsx scripts/health-check.ts --verbose     # 詳細出力
 *   npx tsx scripts/health-check.ts --json        # JSON 出力（自動監視用）
 */
import { initCetusSDK, TickMath, ClmmPoolUtil } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { SuiClient, getFullnodeUrl } from '@mysten/sui/client'
import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'
import BN from 'bn.js'
import * as fs from 'fs'
import * as path from 'path'
import 'dotenv/config'

const POOL = '0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105'
const COIN_A = '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC'
const COIN_B = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
const DA = 6, DB = 9

// Thresholds
const MIN_SUI_FOR_GAS = 0.15     // SUI
const WARN_SUI_FOR_GAS = 0.5     // SUI
const MAX_LOG_AGE_SEC = 120       // ログが120秒以上古ければ警告
const POOL_FEE_RATE = 0.0025     // 0.25%

interface CheckResult {
  name: string
  status: 'OK' | 'WARN' | 'FAIL'
  message: string
  data?: Record<string, unknown>
}

const verbose = process.argv.includes('--verbose')
const jsonOutput = process.argv.includes('--json')

async function main() {
  const results: CheckResult[] = []

  // --- Setup ---
  const privateKey = process.env.SUI_PRIVATE_KEY
  if (!privateKey) {
    results.push({ name: 'ENV', status: 'FAIL', message: 'SUI_PRIVATE_KEY not set' })
    output(results)
    return
  }

  const managedIds = (process.env.POSITION_IDS ?? '').split(',').map(s => s.trim()).filter(Boolean)
  if (managedIds.length === 0) {
    results.push({ name: 'ENV', status: 'WARN', message: 'POSITION_IDS not set — bot will manage all positions' })
  } else {
    results.push({ name: 'ENV', status: 'OK', message: `POSITION_IDS configured: ${managedIds.length} position(s)` })
  }

  const keypair = Ed25519Keypair.fromSecretKey(privateKey)
  const addr = keypair.getPublicKey().toSuiAddress()
  const client = new SuiClient({ url: getFullnodeUrl('mainnet') })
  const sdk = initCetusSDK({ network: 'mainnet' })

  // --- 1. Pool State ---
  const pool = await sdk.Pool.getPool(POOL)
  const suiPriceRaw = TickMath.sqrtPriceX64ToPrice(
    new BN(pool.current_sqrt_price.toString()), DA, DB,
  ).toNumber()
  const suiPrice = 1 / suiPriceRaw
  const currentTick = Number(pool.current_tick_index)

  results.push({
    name: 'POOL',
    status: 'OK',
    message: `SUI price: $${suiPrice.toFixed(4)}, tick: ${currentTick}`,
    data: { suiPrice, currentTick, poolLiquidity: pool.liquidity.toString() },
  })

  // --- 2. Wallet Balances ---
  const [balA, balB] = await Promise.all([
    client.getBalance({ owner: addr, coinType: COIN_A }),
    client.getBalance({ owner: addr, coinType: COIN_B }),
  ])
  const walletUsdc = Number(balA.totalBalance) / 1e6
  const walletSui = Number(balB.totalBalance) / 1e9
  const walletValue = walletUsdc + walletSui * suiPrice

  // Gas check
  if (walletSui < MIN_SUI_FOR_GAS) {
    results.push({ name: 'GAS', status: 'FAIL', message: `SUI balance critically low: ${walletSui.toFixed(4)} SUI < ${MIN_SUI_FOR_GAS} minimum` })
  } else if (walletSui < WARN_SUI_FOR_GAS) {
    results.push({ name: 'GAS', status: 'WARN', message: `SUI balance low: ${walletSui.toFixed(4)} SUI` })
  } else {
    results.push({ name: 'GAS', status: 'OK', message: `SUI for gas: ${walletSui.toFixed(4)} SUI ($${(walletSui * suiPrice).toFixed(2)})` })
  }

  results.push({
    name: 'WALLET',
    status: 'OK',
    message: `USDC: ${walletUsdc.toFixed(4)}, SUI: ${walletSui.toFixed(4)} ($${walletValue.toFixed(2)})`,
    data: { walletUsdc, walletSui, walletValue },
  })

  // --- 3. Position Check ---
  const positions = await sdk.Position.getPositionList(addr, [POOL])
  const curSqrt = new BN(pool.current_sqrt_price.toString())
  let totalManagedValue = 0
  let managedPositionCount = 0

  for (const p of positions) {
    const liq = new BN(p.liquidity.toString())
    const isManaged = managedIds.length === 0 || managedIds.includes(p.pos_object_id)

    if (liq.isZero()) continue

    const lSqrt = TickMath.tickIndexToSqrtPriceX64(p.tick_lower_index)
    const uSqrt = TickMath.tickIndexToSqrtPriceX64(p.tick_upper_index)
    const { coinA, coinB } = ClmmPoolUtil.getCoinAmountFromLiquidity(liq, curSqrt, lSqrt, uSqrt, true)
    const amtA = Number(coinA.toString()) / 1e6
    const amtB = Number(coinB.toString()) / 1e9
    const value = amtA + amtB * suiPrice

    const priceLowRaw = TickMath.tickIndexToPrice(p.tick_lower_index, DA, DB).toNumber()
    const priceHighRaw = TickMath.tickIndexToPrice(p.tick_upper_index, DA, DB).toNumber()
    const priceLow = 1 / priceHighRaw
    const priceHigh = 1 / priceLowRaw

    const inRange = suiPrice >= priceLow && suiPrice <= priceHigh

    // Distance to range edge (%)
    const distToLower = (suiPrice - priceLow) / (priceHigh - priceLow) * 100
    const distToUpper = (priceHigh - suiPrice) / (priceHigh - priceLow) * 100
    const nearEdge = Math.min(distToLower, distToUpper) < 10 // within 10% of edge

    if (isManaged) {
      managedPositionCount++
      totalManagedValue += value

      let status: 'OK' | 'WARN' | 'FAIL' = 'OK'
      let msg = ''

      if (!inRange) {
        status = 'FAIL'
        msg = `OUT OF RANGE! Price $${suiPrice.toFixed(4)} outside [${priceLow.toFixed(4)}, ${priceHigh.toFixed(4)}]`
      } else if (nearEdge) {
        status = 'WARN'
        msg = `Near edge (${Math.min(distToLower, distToUpper).toFixed(1)}% to ${distToLower < distToUpper ? 'lower' : 'upper'})`
      } else {
        msg = `In range (${distToLower.toFixed(1)}%↓ ${distToUpper.toFixed(1)}%↑)`
      }

      results.push({
        name: `POS:${p.pos_object_id.slice(0, 10)}`,
        status,
        message: `$${value.toFixed(2)} | USDC:${amtA.toFixed(2)} SUI:${amtB.toFixed(2)} | ${msg}`,
        data: {
          positionId: p.pos_object_id,
          value,
          usdc: amtA,
          sui: amtB,
          tickLower: p.tick_lower_index,
          tickUpper: p.tick_upper_index,
          priceLower: priceLow,
          priceUpper: priceHigh,
          inRange,
          distToLower: distToLower.toFixed(1),
          distToUpper: distToUpper.toFixed(1),
          liquidity: p.liquidity.toString(),
        },
      })
    }
  }

  if (managedPositionCount === 0) {
    results.push({ name: 'POSITION', status: 'FAIL', message: 'No managed positions found with liquidity' })
  }

  // --- 4. Fund Accounting ---
  const totalValue = totalManagedValue + walletValue
  results.push({
    name: 'FUNDS',
    status: 'OK',
    message: `Position: $${totalManagedValue.toFixed(2)} + Wallet: $${walletValue.toFixed(2)} = Total: $${totalValue.toFixed(2)}`,
    data: { positionValue: totalManagedValue, walletValue, totalValue },
  })

  // --- 5. Bot Log Check ---
  const logDir = path.join(process.cwd(), 'logs')
  const today = new Date().toISOString().slice(0, 10)
  const eventLogPath = path.join(logDir, `events-${today}.jsonl`)
  const botLogPath = path.join(logDir, 'bot.log')

  // Check event log
  if (fs.existsSync(eventLogPath)) {
    const stat = fs.statSync(eventLogPath)
    const ageSec = (Date.now() - stat.mtimeMs) / 1000
    const lines = fs.readFileSync(eventLogPath, 'utf-8').trim().split('\n')
    const eventCount = lines.length

    // Count event types
    let errors = 0
    let rebalances = 0
    let checks = 0
    for (const line of lines) {
      try {
        const evt = JSON.parse(line)
        if (evt.type?.includes('error') || evt.type === 'scheduler_halt') errors++
        if (evt.type === 'rebalance_complete') rebalances++
        if (evt.type === 'rebalance_check') checks++
      } catch { }
    }

    results.push({
      name: 'EVENT_LOG',
      status: errors > 0 ? 'WARN' : 'OK',
      message: `${eventCount} events today | checks: ${checks}, rebalances: ${rebalances}, errors: ${errors}`,
      data: { eventCount, checks, rebalances, errors, lastModifiedSec: Math.floor(ageSec) },
    })
  } else {
    results.push({ name: 'EVENT_LOG', status: 'WARN', message: `No event log for today (${today})` })
  }

  // Check bot.log recency
  if (fs.existsSync(botLogPath)) {
    const stat = fs.statSync(botLogPath)
    const ageSec = (Date.now() - stat.mtimeMs) / 1000

    if (ageSec > MAX_LOG_AGE_SEC) {
      results.push({
        name: 'BOT_ALIVE',
        status: 'WARN',
        message: `bot.log not updated for ${Math.floor(ageSec)}s — bot may not be running`,
      })
    } else {
      results.push({
        name: 'BOT_ALIVE',
        status: 'OK',
        message: `bot.log updated ${Math.floor(ageSec)}s ago — bot appears active`,
      })
    }

    // Check last few lines for errors
    const tail = fs.readFileSync(botLogPath, 'utf-8').trim().split('\n').slice(-5)
    const hasError = tail.some(l => l.includes('[error]') || l.includes('CRITICAL'))
    if (hasError) {
      results.push({
        name: 'BOT_ERRORS',
        status: 'WARN',
        message: 'Recent errors in bot.log: ' + tail.filter(l => l.includes('[error]')).join(' | ').slice(0, 200),
      })
    }
  } else {
    results.push({
      name: 'BOT_ALIVE',
      status: 'WARN',
      message: 'bot.log not found — bot may not be running (or using different log path)',
    })
  }

  // --- 6. Cost Estimate (next rebalance) ---
  const estimatedSwapAmount = totalManagedValue * 0.4 // ~40% of position
  const estimatedSwapCost = estimatedSwapAmount * POOL_FEE_RATE
  const estimatedGas = 0.012 * suiPrice // ~0.012 SUI for 3 TXs
  const estimatedRebalanceCost = estimatedSwapCost + estimatedGas

  results.push({
    name: 'COST_EST',
    status: 'OK',
    message: `Next rebalance est. cost: $${estimatedRebalanceCost.toFixed(4)} (swap fee: $${estimatedSwapCost.toFixed(4)} + gas: $${estimatedGas.toFixed(4)})`,
    data: { estimatedSwapCost, estimatedGas, estimatedRebalanceCost },
  })

  // --- Output ---
  output(results)
}

function output(results: CheckResult[]) {
  if (jsonOutput) {
    const summary = {
      timestamp: new Date().toISOString(),
      overall: results.some(r => r.status === 'FAIL') ? 'FAIL' : results.some(r => r.status === 'WARN') ? 'WARN' : 'OK',
      checks: results.map(r => ({ name: r.name, status: r.status, message: r.message, ...r.data })),
    }
    console.log(JSON.stringify(summary, null, 2))
    return
  }

  const W = 70
  console.log('='.repeat(W))
  console.log('  Sui Auto LP - Health Check')
  console.log('  ' + new Date().toISOString())
  console.log('='.repeat(W))
  console.log()

  const icons = { OK: '[OK]  ', WARN: '[WARN]', FAIL: '[FAIL]' }

  for (const r of results) {
    console.log(`  ${icons[r.status]} ${r.name}`)
    console.log(`         ${r.message}`)
    if (verbose && r.data) {
      for (const [k, v] of Object.entries(r.data)) {
        console.log(`           ${k}: ${v}`)
      }
    }
    console.log()
  }

  // Summary
  const fails = results.filter(r => r.status === 'FAIL').length
  const warns = results.filter(r => r.status === 'WARN').length
  const oks = results.filter(r => r.status === 'OK').length

  console.log('-'.repeat(W))
  if (fails > 0) {
    console.log(`  RESULT: FAIL (${fails} failure(s), ${warns} warning(s), ${oks} OK)`)
  } else if (warns > 0) {
    console.log(`  RESULT: WARN (${warns} warning(s), ${oks} OK)`)
  } else {
    console.log(`  RESULT: ALL OK (${oks} checks passed)`)
  }
  console.log('='.repeat(W))
}

main().catch(err => {
  console.error('Health check failed:', err.message || err)
  process.exit(1)
})
