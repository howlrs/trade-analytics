import { loadConfig } from './config/index.js'
import { initLogger, getLogger } from './utils/logger.js'
import { loadKeypair } from './utils/wallet.js'
import { initSuiClient } from './utils/sui.js'
import { initCetusSdk, initAggregatorClient } from './core/pool.js'
import { startScheduler } from './scheduler.js'
import { discoverPositions } from './core/discover.js'

async function main() {
  // Load and validate config
  const config = loadConfig()
  const log = initLogger(config.logLevel)

  log.info('Sui Auto LP starting', {
    network: config.network,
    pools: config.pools.length,
    dryRun: config.dryRun,
  })

  // Initialize wallet
  const keypair = loadKeypair(config.privateKey)
  const address = keypair.getPublicKey().toSuiAddress()
  log.info('Wallet address', { address })

  // Initialize clients
  const suiClient = initSuiClient(config.network)
  initCetusSdk(config.network, address)
  initAggregatorClient(config.network, suiClient)

  // Auto-discover and validate positions on-chain
  await discoverPositions(config, address)

  // Start scheduler
  const stop = startScheduler(config, keypair)

  // Graceful shutdown
  const shutdown = () => {
    log.info('Shutting down...')
    stop()
    process.exit(0)
  }
  process.on('SIGINT', shutdown)
  process.on('SIGTERM', shutdown)
}

main().catch(err => {
  console.error('Fatal error:', err)
  process.exit(1)
})
