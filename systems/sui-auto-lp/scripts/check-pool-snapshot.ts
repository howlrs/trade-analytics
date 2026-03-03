import { initCetusSDK, d } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { getFullnodeUrl, SuiClient } from '@mysten/sui/client'
import 'dotenv/config'

const sdk = initCetusSDK({ network: 'mainnet' })

const poolId = '0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105'
const address = '0x4706d9a122d8ef132a78ee5455837da406c2b61144dc1d59bbbbedb1abd3c466'

async function main() {
  const pool = await sdk.Pool.getPool(poolId)
  const currentTick = Number(pool.current_tick_index)

  // Price: coinB per coinA (SUI per USDC). USD = 1/price
  const sqrtPrice = BigInt(pool.current_sqrt_price)
  const price = Number(sqrtPrice * sqrtPrice) / (2 ** 128) * (10 ** (6 - 9))
  const suiPriceUsd = 1 / price
  console.log(`Pool tick: ${currentTick}`)
  console.log(`Price (SUI/USDC): ${price.toFixed(6)} → SUI = $${suiPriceUsd.toFixed(4)}`)

  const positions = await sdk.Position.getPositionList(address, [poolId])
  let totalLpUsd = 0

  for (const pos of positions) {
    const liq = BigInt(pos.liquidity)
    if (liq === 0n) continue

    const tickLower = Number(pos.tick_lower_index)
    const tickUpper = Number(pos.tick_upper_index)
    const inRange = currentTick >= tickLower && currentTick < tickUpper
    const feeA = Number(pos.fee_owed_a) / 1e6
    const feeB = Number(pos.fee_owed_b) / 1e9

    // Estimate position value from pre-open balances (logged)
    // amountA_USDC: 1489.9270, amountB_SUI: 1872.1066
    const posValueUsd = 1489.93 + 1872.11 * suiPriceUsd

    totalLpUsd = posValueUsd

    console.log(`\n--- Position: ${pos.pos_object_id.slice(0, 10)}...`)
    console.log(`  Tick: ${tickLower} - ${tickUpper} ${inRange ? '✓ IN RANGE' : '✗ OUT'}`)
    console.log(`  Liquidity: ${liq.toString()}`)
    console.log(`  Fees: ${feeA.toFixed(4)} USDC + ${feeB.toFixed(4)} SUI`)
    console.log(`  Est. value: ~$${posValueUsd.toFixed(2)}`)
  }

  // Wallet
  const suiClient = new SuiClient({ url: getFullnodeUrl('mainnet') })
  const [suiBal, usdcBal] = await Promise.all([
    suiClient.getBalance({ owner: address }),
    suiClient.getBalance({ owner: address, coinType: '0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC' }),
  ])
  const walletSui = Number(suiBal.totalBalance) / 1e9
  const walletUsdc = Number(usdcBal.totalBalance) / 1e6
  const walletUsd = walletSui * suiPriceUsd + walletUsdc

  console.log(`\n━━━ Summary ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`)
  console.log(`SUI price: $${suiPriceUsd.toFixed(4)}`)
  console.log(`Wallet: ${walletSui.toFixed(4)} SUI + ${walletUsdc.toFixed(2)} USDC = $${walletUsd.toFixed(2)}`)
  console.log(`LP position: ~$${totalLpUsd.toFixed(2)}`)
  console.log(`Total: ~$${(walletUsd + totalLpUsd).toFixed(2)}`)
}
main().catch(console.error)
