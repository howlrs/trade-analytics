/**
 * Cleanup: Remove liquidity from the debug test position and verify state.
 */
import { initCetusSDK, TickMath, ClmmPoolUtil } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { SuiClient, getFullnodeUrl } from '@mysten/sui/client'
import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'
import BN from 'bn.js'
import 'dotenv/config'

const POOL = '0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105'
const COIN_A = '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC'
const COIN_B = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
const DA = 6, DB = 9

// Debug position created during testing
const DEBUG_POS = '0xa5f0a85d205b28d4f3f2a52af13bd02c01a31e98aef556eff9e083a91c86d8c2'

async function main() {
  const privateKey = process.env.SUI_PRIVATE_KEY
  if (!privateKey) throw new Error('SUI_PRIVATE_KEY not set')

  const keypair = Ed25519Keypair.fromSecretKey(privateKey)
  const addr = keypair.getPublicKey().toSuiAddress()
  const client = new SuiClient({ url: getFullnodeUrl('mainnet') })
  const sdk = initCetusSDK({ network: 'mainnet' })
  sdk.senderAddress = addr

  // Find the debug position
  const positions = await sdk.Position.getPositionList(addr, [POOL])
  const debugPos = positions.find(p => p.pos_object_id === DEBUG_POS)

  if (!debugPos || BigInt(debugPos.liquidity.toString()) === 0n) {
    console.log('Debug position already empty or not found')
  } else {
    console.log('Removing liquidity from debug position...')
    console.log('  Liquidity:', debugPos.liquidity.toString())

    const payload = await sdk.Position.removeLiquidityTransactionPayload({
      coinTypeA: COIN_A,
      coinTypeB: COIN_B,
      delta_liquidity: debugPos.liquidity.toString(),
      min_amount_a: '0',
      min_amount_b: '0',
      collect_fee: true,
      rewarder_coin_types: [],
      pool_id: POOL,
      pos_id: DEBUG_POS,
    })

    const result = await sdk.fullClient.sendTransaction(keypair as any, payload)
    console.log('  TX:', result?.digest)
    console.log('  Done - funds returned to wallet')
    await new Promise(r => setTimeout(r, 3000))
  }

  // Show final state
  const pool = await sdk.Pool.getPool(POOL)
  const suiPriceRaw = TickMath.sqrtPriceX64ToPrice(
    new BN(pool.current_sqrt_price.toString()), DA, DB
  ).toNumber()
  const suiPrice = 1 / suiPriceRaw

  console.log()
  console.log('=== Final State (SUI: $' + suiPrice.toFixed(4) + ') ===')

  const allPos = await sdk.Position.getPositionList(addr, [POOL])
  for (const p of allPos) {
    const liq = new BN(p.liquidity.toString())
    if (liq.isZero()) {
      console.log(p.pos_object_id.slice(0, 12) + '... [EMPTY]')
      continue
    }
    const curSqrt = new BN(pool.current_sqrt_price.toString())
    const lSqrt = TickMath.tickIndexToSqrtPriceX64(p.tick_lower_index)
    const uSqrt = TickMath.tickIndexToSqrtPriceX64(p.tick_upper_index)
    const { coinA, coinB } = ClmmPoolUtil.getCoinAmountFromLiquidity(liq, curSqrt, lSqrt, uSqrt, true)
    const amtA = Number(coinA.toString()) / 1e6
    const amtB = Number(coinB.toString()) / 1e9
    const value = amtA + amtB * suiPrice

    const isLarge = p.pos_object_id === '0xff1ead7cb5d7f29099c985df5ff60749bd20b70e9418cb893897df4e69f67113'
    console.log(p.pos_object_id.slice(0, 12) + '...' + (isLarge ? ' [LARGE]' : ''))
    console.log('  USDC: ' + amtA.toFixed(4) + ' + SUI: ' + amtB.toFixed(4) + ' = $' + value.toFixed(2))
  }

  const [u, s] = await Promise.all([
    client.getBalance({ owner: addr, coinType: COIN_A }),
    client.getBalance({ owner: addr, coinType: COIN_B }),
  ])
  const wUsdc = Number(u.totalBalance) / 1e6
  const wSui = Number(s.totalBalance) / 1e9
  console.log()
  console.log('Wallet: ' + wUsdc.toFixed(4) + ' USDC + ' + wSui.toFixed(4) + ' SUI ($' + (wUsdc + wSui * suiPrice).toFixed(2) + ')')
}

main().catch(err => {
  console.error('Error:', err.message || err)
  process.exit(1)
})
