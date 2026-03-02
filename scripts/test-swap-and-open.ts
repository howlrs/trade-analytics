/**
 * Test script: Swap SUI → USDC, then open a narrow LP position
 * Target: $10 SUI + $10 USDC in USDC/SUI pool
 */
import { initCetusSDK } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { SuiClient, getFullnodeUrl } from '@mysten/sui/client'
import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'
import { TickMath } from '@cetusprotocol/cetus-sui-clmm-sdk'
import BN from 'bn.js'
import 'dotenv/config'

const POOL_ID = '0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105'
const COIN_TYPE_A = '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC'
const COIN_TYPE_B = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
const DECIMALS_A = 6 // USDC
const DECIMALS_B = 9 // SUI
const TICK_SPACING = 60

// How much USDC we want in total
const TARGET_USDC = 10_000_000 // 10 USDC in raw (6 decimals)
// How much SUI for LP ($10 worth)
const LP_SUI_RAW = 9_700_000_000n // ~9.7 SUI
const LP_USDC_RAW = 10_000_000n   // 10 USDC

async function main() {
  const DRY_RUN = process.argv.includes('--dry-run')
  console.log(`Mode: ${DRY_RUN ? 'DRY-RUN' : 'LIVE EXECUTION'}`)
  console.log()

  // --- Init ---
  const privateKey = process.env.SUI_PRIVATE_KEY
  if (!privateKey) throw new Error('SUI_PRIVATE_KEY not set in .env')

  let keypair: Ed25519Keypair
  if (privateKey.startsWith('suiprivkey')) {
    keypair = Ed25519Keypair.fromSecretKey(privateKey)
  } else {
    const raw = Buffer.from(privateKey, 'base64')
    keypair = Ed25519Keypair.fromSecretKey(raw.length === 33 ? raw.subarray(1) : raw)
  }
  const address = keypair.getPublicKey().toSuiAddress()
  console.log(`Wallet: ${address}`)

  const client = new SuiClient({ url: getFullnodeUrl('mainnet') })
  const sdk = initCetusSDK({ network: 'mainnet' })
  sdk.senderAddress = address

  // --- Check balances ---
  const [balA, balB] = await Promise.all([
    client.getBalance({ owner: address, coinType: COIN_TYPE_A }),
    client.getBalance({ owner: address, coinType: COIN_TYPE_B }),
  ])
  const usdcBal = BigInt(balA.totalBalance)
  const suiBal = BigInt(balB.totalBalance)
  console.log(`Balance: ${Number(usdcBal) / 1e6} USDC, ${Number(suiBal) / 1e9} SUI`)

  // --- Step 1: Swap SUI → USDC if needed ---
  const usdcNeeded = BigInt(TARGET_USDC) - usdcBal
  if (usdcNeeded > 100_000n) { // > 0.1 USDC
    console.log()
    console.log(`=== Step 1: Swap SUI → USDC ===`)
    console.log(`USDC needed: ${Number(usdcNeeded) / 1e6} USDC`)

    // Get pool for preswap
    const pool = await sdk.Pool.getPool(POOL_ID)

    // Calculate SUI amount to swap
    // Pool: USDC(A,6dec)/SUI(B,9dec), tick price = SUI per USDC
    // We need USDC, so we're selling SUI (B→A, a2b=false)
    // suiPerUsdc ≈ 0.97 means 1 USDC costs ~0.97 SUI
    const currentSqrtPrice = Number(pool.current_sqrt_price)
    const rawPrice = (currentSqrtPrice / (2 ** 64)) ** 2
    const suiPerUsdc = rawPrice * (10 ** (DECIMALS_A - DECIMALS_B)) // ~0.97
    // usdcNeeded is in raw (6 dec), convert to human, multiply by suiPerUsdc, convert back to raw SUI (9 dec)
    const usdcHuman = Number(usdcNeeded) / 1e6
    const suiHuman = usdcHuman * suiPerUsdc * 1.02 // 2% buffer
    const swapSuiAmount = Math.ceil(suiHuman * 1e9)
    console.log(`Swap amount: ${swapSuiAmount / 1e9} SUI → ~${Number(usdcNeeded) / 1e6} USDC`)

    // PreSwap: SUI(B) → USDC(A), a2b=false
    const preSwapResult = await sdk.Swap.preswap({
      pool,
      currentSqrtPrice: Number(pool.current_sqrt_price),
      decimalsA: DECIMALS_A,
      decimalsB: DECIMALS_B,
      a2b: false, // B→A = SUI→USDC
      byAmountIn: true,
      amount: swapSuiAmount.toString(),
      coinTypeA: COIN_TYPE_A,
      coinTypeB: COIN_TYPE_B,
    })

    if (!preSwapResult || preSwapResult.isExceed) {
      throw new Error(`PreSwap failed: ${preSwapResult?.isExceed ? 'exceeds liquidity' : 'null result'}`)
    }

    const estimatedOut = BigInt(preSwapResult.estimatedAmountOut)
    const amountLimit = estimatedOut * 99n / 100n // 1% slippage
    console.log(`Estimated output: ${Number(estimatedOut) / 1e6} USDC`)
    console.log(`Amount limit (1% slippage): ${Number(amountLimit) / 1e6} USDC`)

    // Create swap tx
    const swapPayload = await sdk.Swap.createSwapTransactionPayload({
      pool_id: POOL_ID,
      coinTypeA: COIN_TYPE_A,
      coinTypeB: COIN_TYPE_B,
      a2b: false,
      by_amount_in: true,
      amount: swapSuiAmount.toString(),
      amount_limit: amountLimit.toString(),
    })

    if (DRY_RUN) {
      // Dry-run
      const txBytes = await (swapPayload as any).build({ client })
      const dryResult = await client.dryRunTransactionBlock({
        transactionBlock: Buffer.from(txBytes).toString('base64'),
      })
      const gasUsed = dryResult.effects.gasUsed
      const gas = BigInt(gasUsed.computationCost) + BigInt(gasUsed.storageCost) - BigInt(gasUsed.storageRebate)
      console.log(`Swap dry-run: ${dryResult.effects.status.status} (gas: ${Number(gas) / 1e9} SUI)`)
      if (dryResult.effects.status.status !== 'success') {
        console.error(`Error: ${dryResult.effects.status.error}`)
        return
      }
    } else {
      const result = await sdk.fullClient.sendTransaction(keypair as any, swapPayload)
      console.log(`Swap TX: ${result?.digest}`)
      if (!result) throw new Error('Swap failed')
      // Wait a moment for state to settle
      await new Promise(r => setTimeout(r, 3000))
    }
  } else {
    console.log(`\nStep 1: Swap not needed (USDC balance sufficient)`)
  }

  // --- Refresh balances ---
  const [balA2, balB2] = await Promise.all([
    client.getBalance({ owner: address, coinType: COIN_TYPE_A }),
    client.getBalance({ owner: address, coinType: COIN_TYPE_B }),
  ])
  const usdcBal2 = BigInt(balA2.totalBalance)
  const suiBal2 = BigInt(balB2.totalBalance)
  console.log(`\nPost-swap balance: ${Number(usdcBal2) / 1e6} USDC, ${Number(suiBal2) / 1e9} SUI`)

  // --- Step 2: Open LP position ---
  console.log()
  console.log(`=== Step 2: Open test LP position ===`)

  // Get fresh pool data
  const pool = await sdk.Pool.getPool(POOL_ID)
  const currentTick = pool.current_tick_index

  // Calculate ±3% range
  // tick_to_sui_price = 1 / (1.0001^tick * 10^(decA-decB))
  const currentSuiPrice = 1 / ((1.0001 ** currentTick) * (10 ** (DECIMALS_A - DECIMALS_B)))
  const suiPriceLower = currentSuiPrice * 0.97
  const suiPriceUpper = currentSuiPrice * 1.03

  // Convert SUI prices to ticks (lower SUI price → higher tick)
  function suiPriceToTick(suiPrice: number): number {
    const suiPerUsdc = 1 / suiPrice
    const rawPrice = suiPerUsdc / (10 ** (DECIMALS_A - DECIMALS_B))
    const rawTick = Math.log(rawPrice) / Math.log(1.0001)
    return Math.round(rawTick / TICK_SPACING) * TICK_SPACING
  }

  const tickLower = suiPriceToTick(suiPriceUpper) // higher SUI price → lower tick
  const tickUpper = suiPriceToTick(suiPriceLower) // lower SUI price → higher tick

  const actualLower = 1 / ((1.0001 ** tickLower) * (10 ** (DECIMALS_A - DECIMALS_B)))
  const actualUpper = 1 / ((1.0001 ** tickUpper) * (10 ** (DECIMALS_A - DECIMALS_B)))

  console.log(`Current SUI price: $${currentSuiPrice.toFixed(4)}`)
  console.log(`Range: $${actualUpper.toFixed(4)} ~ $${actualLower.toFixed(4)} (±3%)`)
  console.log(`Ticks: [${tickLower}, ${tickUpper}]`)

  // Determine amounts for LP ($10 each side)
  const lpUsdc = Math.min(Number(usdcBal2), TARGET_USDC).toString()
  const gasReserve = 500_000_000n // 0.5 SUI for gas
  const lpSui = Math.min(Number(suiBal2 - gasReserve), Number(LP_SUI_RAW)).toString()

  console.log(`LP input: ${Number(lpUsdc) / 1e6} USDC + ${Number(lpSui) / 1e9} SUI`)

  // Create add liquidity payload
  const addLiqPayload = await sdk.Position.createAddLiquidityFixTokenPayload({
    pool_id: POOL_ID,
    coinTypeA: COIN_TYPE_A,
    coinTypeB: COIN_TYPE_B,
    tick_lower: tickLower,
    tick_upper: tickUpper,
    is_open: true,
    pos_id: '',
    fix_amount_a: true,
    amount_a: lpUsdc,
    amount_b: lpSui,
    slippage: 0.01,
    collect_fee: false,
    rewarder_coin_types: [],
  })

  if (DRY_RUN) {
    ;(addLiqPayload as any).setSender(address)
    const txBytes = await (addLiqPayload as any).build({ client })
    const dryResult = await client.dryRunTransactionBlock({
      transactionBlock: Buffer.from(txBytes).toString('base64'),
    })
    const gasUsed = dryResult.effects.gasUsed
    const gas = BigInt(gasUsed.computationCost) + BigInt(gasUsed.storageCost) - BigInt(gasUsed.storageRebate)
    console.log(`Open LP dry-run: ${dryResult.effects.status.status} (gas: ${Number(gas) / 1e9} SUI)`)
    if (dryResult.effects.status.status !== 'success') {
      console.error(`Error: ${dryResult.effects.status.error}`)
    } else {
      console.log()
      console.log('=== DRY-RUN COMPLETE ===')
      console.log('Both swap and LP creation would succeed.')
      console.log('Run without --dry-run to execute for real.')
    }
  } else {
    const result = await sdk.fullClient.sendTransaction(keypair as any, addLiqPayload)
    console.log(`Open LP TX: ${result?.digest}`)
    if (!result) throw new Error('Open LP failed')
    console.log()
    console.log('=== TEST POSITION CREATED ===')
    console.log(`Swap TX + LP TX executed on mainnet`)
    console.log(`Check position at: https://app.cetus.zone/liquidity`)
  }
}

main().catch(err => {
  console.error('FATAL:', err.message || err)
  process.exit(1)
})
