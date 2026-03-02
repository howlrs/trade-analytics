/**
 * Resize a position to a target USD value by removing excess liquidity.
 * Usage: npx tsx scripts/resize-position.ts [targetUSD]
 * Default target: $20
 */
import { initCetusSDK, TickMath, ClmmPoolUtil } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'
import BN from 'bn.js'
import 'dotenv/config'

const POOL = '0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105'
const COIN_A = '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC'
const COIN_B = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
const DA = 6, DB = 9

async function main() {
  const targetUSD = Number(process.argv[2]) || 20
  const positionId = process.env.POSITION_IDS?.split(',')[0]?.trim()
  if (!positionId) throw new Error('POSITION_IDS not set in .env')

  const privateKey = process.env.SUI_PRIVATE_KEY
  if (!privateKey) throw new Error('SUI_PRIVATE_KEY not set')

  const keypair = Ed25519Keypair.fromSecretKey(privateKey)
  const addr = keypair.getPublicKey().toSuiAddress()
  const sdk = initCetusSDK({ network: 'mainnet' })
  sdk.senderAddress = addr

  const pool = await sdk.Pool.getPool(POOL)
  const suiPriceRaw = TickMath.sqrtPriceX64ToPrice(
    new BN(pool.current_sqrt_price.toString()), DA, DB
  ).toNumber()
  const suiPrice = 1 / suiPriceRaw

  console.log('SUI price: $' + suiPrice.toFixed(4))

  // Find the position
  const positions = await sdk.Position.getPositionList(addr, [POOL])
  const pos = positions.find(p => p.pos_object_id === positionId)
  if (!pos) throw new Error('Position not found: ' + positionId)

  const liq = new BN(pos.liquidity.toString())
  if (liq.isZero()) {
    console.log('Position is empty, nothing to resize.')
    return
  }

  // Calculate current value
  const curSqrt = new BN(pool.current_sqrt_price.toString())
  const lSqrt = TickMath.tickIndexToSqrtPriceX64(pos.tick_lower_index)
  const uSqrt = TickMath.tickIndexToSqrtPriceX64(pos.tick_upper_index)
  const { coinA, coinB } = ClmmPoolUtil.getCoinAmountFromLiquidity(liq, curSqrt, lSqrt, uSqrt, true)
  const amtA = Number(coinA.toString()) / 1e6
  const amtB = Number(coinB.toString()) / 1e9
  const currentValue = amtA + amtB * suiPrice

  console.log('Position: ' + positionId.slice(0, 16) + '...')
  console.log('  Current: ' + amtA.toFixed(4) + ' USDC + ' + amtB.toFixed(4) + ' SUI = $' + currentValue.toFixed(2))
  console.log('  Target:  $' + targetUSD.toFixed(2))

  if (currentValue <= targetUSD * 1.05) {
    console.log('Position is already at or below target. No action needed.')
    return
  }

  // Calculate how much liquidity to remove
  // removeRatio = (currentValue - targetUSD) / currentValue
  const removeRatio = (currentValue - targetUSD) / currentValue
  const removeLiquidity = new BN(
    (BigInt(pos.liquidity.toString()) * BigInt(Math.floor(removeRatio * 10000)) / 10000n).toString()
  )

  const removeA = Number(coinA.toString()) * removeRatio / 1e6
  const removeB = Number(coinB.toString()) * removeRatio / 1e9
  const removeValue = removeA + removeB * suiPrice

  console.log()
  console.log('Plan:')
  console.log('  Remove ' + (removeRatio * 100).toFixed(1) + '% of liquidity')
  console.log('  ≈ ' + removeA.toFixed(4) + ' USDC + ' + removeB.toFixed(4) + ' SUI ($' + removeValue.toFixed(2) + ')')
  console.log('  Remaining ≈ $' + (currentValue - removeValue).toFixed(2))
  console.log()

  // Confirm
  console.log('Executing partial remove...')

  const payload = await sdk.Position.removeLiquidityTransactionPayload({
    coinTypeA: COIN_A,
    coinTypeB: COIN_B,
    delta_liquidity: removeLiquidity.toString(),
    min_amount_a: '0',
    min_amount_b: '0',
    collect_fee: true,
    rewarder_coin_types: [],
    pool_id: POOL,
    pos_id: positionId,
  })

  const result = await sdk.fullClient.sendTransaction(keypair as any, payload)
  console.log('TX:', result?.digest)

  // Verify
  await new Promise(r => setTimeout(r, 3000))
  const positions2 = await sdk.Position.getPositionList(addr, [POOL])
  const pos2 = positions2.find(p => p.pos_object_id === positionId)
  if (pos2) {
    const liq2 = new BN(pos2.liquidity.toString())
    const { coinA: a2, coinB: b2 } = ClmmPoolUtil.getCoinAmountFromLiquidity(
      liq2, curSqrt, lSqrt, uSqrt, true,
    )
    const val2 = Number(a2.toString()) / 1e6 + (Number(b2.toString()) / 1e9) * suiPrice
    console.log('After resize: $' + val2.toFixed(2))
  }
}

main().catch(err => {
  console.error('Error:', err.message || err)
  process.exit(1)
})
