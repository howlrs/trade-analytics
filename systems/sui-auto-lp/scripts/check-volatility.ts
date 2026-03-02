import { initCetusSDK } from '@cetusprotocol/cetus-sui-clmm-sdk'
import { initSuiClient } from '../src/utils/sui.js'
import { calculateVolatilityBasedTicks } from '../src/strategy/volatility.js'
import { initLogger } from '../src/utils/logger.js'

const POOL_ID = '0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105'

async function main() {
  initLogger('info')
  initSuiClient('mainnet')
  const sdk = initCetusSDK({ network: 'mainnet' })
  const pool = await sdk.Pool.getPool(POOL_ID)
  const tickSpacing = Number(pool.tickSpacing)

  console.log('Pool tickSpacing:', tickSpacing)
  console.log('Current tick:', pool.current_tick_index)
  console.log()

  const result = await calculateVolatilityBasedTicks(POOL_ID, tickSpacing, 2, 60, 240)
  if (result) {
    console.log('\nVolatility engine result:')
    console.log('  σ (per hour):', result.sigma.toFixed(2))
    console.log('  Tick width:', result.tickWidth)

    const halfWidth = Math.floor(result.tickWidth / 2)
    const currentTick = pool.current_tick_index
    const newLower = Math.round((currentTick - halfWidth) / tickSpacing) * tickSpacing
    const newUpper = Math.round((currentTick + halfWidth) / tickSpacing) * tickSpacing
    console.log('\nExpected new range:')
    console.log('  Ticks:', newLower, '~', newUpper, '(' + (newUpper - newLower) + ' ticks)')
    console.log('  Current:', 68820, '~', 70020, '(1200 ticks)')
    console.log('  Narrowing:', (1200 / (newUpper - newLower)).toFixed(1) + 'x')
  } else {
    console.log('\nVolatility engine returned null')
  }
}

main().catch(err => { console.error(err); process.exit(1) })
