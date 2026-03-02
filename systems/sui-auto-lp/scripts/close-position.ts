/**
 * Close a specific position: remove all liquidity and return funds to wallet.
 * Usage: npx tsx scripts/close-position.ts [positionId]
 *   If no positionId, uses POSITION_IDS from .env
 */
import { initCetusSDK, TickMath } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { SuiClient, getFullnodeUrl } from '@mysten/sui/client'
import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'
import BN from 'bn.js'
import 'dotenv/config'

const POOL = '0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105'
const COIN_A = '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC'
const COIN_B = '0x0000000000000000000000000000000000000000000000000000000000000002::sui::SUI'
const DA = 6, DB = 9

async function main() {
  const privateKey = process.env.SUI_PRIVATE_KEY
  if (!privateKey) throw new Error('SUI_PRIVATE_KEY not set')

  const posId = process.argv[2] || process.env.POSITION_IDS?.split(',')[0]
  if (!posId) throw new Error('No position ID provided')

  console.log('Target position:', posId)

  const keypair = Ed25519Keypair.fromSecretKey(privateKey)
  const addr = keypair.getPublicKey().toSuiAddress()
  const client = new SuiClient({ url: getFullnodeUrl('mainnet') })
  const sdk = initCetusSDK({ network: 'mainnet' })
  sdk.senderAddress = addr

  // Find the position
  const positions = await sdk.Position.getPositionList(addr, [POOL])
  const pos = positions.find(p => p.pos_object_id === posId)

  if (!pos) {
    console.log('Position not found')
    return
  }

  const liq = BigInt(pos.liquidity.toString())
  if (liq === 0n) {
    console.log('Position already empty (0 liquidity)')
  } else {
    console.log('Removing liquidity:', liq.toString())

    // Get rewarder coin types from pool
    const pool = await sdk.Pool.getPool(POOL)
    const rewarderTypes = (pool as any).rewarder_infos
      ?.filter((r: any) => Number(r.emissions_per_second) > 0)
      ?.map((r: any) => r.coinAddress) ?? []

    const payload = await sdk.Position.removeLiquidityTransactionPayload({
      coinTypeA: COIN_A,
      coinTypeB: COIN_B,
      delta_liquidity: pos.liquidity.toString(),
      min_amount_a: '0',
      min_amount_b: '0',
      collect_fee: true,
      rewarder_coin_types: rewarderTypes,
      pool_id: POOL,
      pos_id: posId,
    })

    const result = await sdk.fullClient.sendTransaction(keypair as any, payload)
    console.log('TX:', result?.digest)
    console.log('Liquidity removed + fees/rewards collected')
    await new Promise(r => setTimeout(r, 3000))
  }

  // Show wallet state
  const [u, s] = await Promise.all([
    client.getBalance({ owner: addr, coinType: COIN_A }),
    client.getBalance({ owner: addr, coinType: COIN_B }),
  ])
  const pool = await sdk.Pool.getPool(POOL)
  const suiPriceRaw = TickMath.sqrtPriceX64ToPrice(
    new BN(pool.current_sqrt_price.toString()), DA, DB
  ).toNumber()
  const suiPrice = 1 / suiPriceRaw

  const wUsdc = Number(u.totalBalance) / 1e6
  const wSui = Number(s.totalBalance) / 1e9
  console.log()
  console.log(`Wallet: ${wUsdc.toFixed(4)} USDC + ${wSui.toFixed(4)} SUI ($${(wUsdc + wSui * suiPrice).toFixed(2)})`)
}

main().catch(err => {
  console.error('Error:', err.message || err)
  process.exit(1)
})
