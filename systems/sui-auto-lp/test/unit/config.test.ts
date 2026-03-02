import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { PoolConfigSchema, ConfigSchema } from '../../src/types/config.js'

// ── PoolConfigSchema ────────────────────────────────────────────────

describe('PoolConfigSchema', () => {
  it('accepts valid minimal input (only poolId)', () => {
    const result = PoolConfigSchema.parse({ poolId: '0xabc123' })
    expect(result.poolId).toBe('0xabc123')
  })

  it('applies all defaults correctly', () => {
    const result = PoolConfigSchema.parse({ poolId: '0xabc' })
    expect(result.strategy).toBe('dynamic')
    expect(result.narrowRangePct).toBe(0.08)
    expect(result.wideRangePct).toBe(0.15)
    expect(result.volLookbackHours).toBe(2)
    expect(result.volTickWidthMin).toBe(480)
    expect(result.volTickWidthMax).toBe(1200)
  })

  it('accepts optional positionIds array', () => {
    const result = PoolConfigSchema.parse({ poolId: '0xabc', positionIds: ['0xpos1'] })
    expect(result.positionIds).toEqual(['0xpos1'])
  })

  it('rejects empty poolId', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '' })).toThrow()
  })

  it('rejects narrowRangePct below 0.001', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', narrowRangePct: 0.0001 })).toThrow()
  })

  it('rejects narrowRangePct above 0.5', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', narrowRangePct: 0.6 })).toThrow()
  })

  it('rejects volTickWidthMin below 60', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', volTickWidthMin: 30 })).toThrow()
  })

  it('rejects non-integer volTickWidthMin', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', volTickWidthMin: 100.5 })).toThrow()
  })

  it('rejects volTickWidthMax below 60', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', volTickWidthMax: 10 })).toThrow()
  })

  it('accepts valid strategy enum values', () => {
    for (const strategy of ['narrow', 'wide', 'dynamic'] as const) {
      const result = PoolConfigSchema.parse({ poolId: '0xabc', strategy })
      expect(result.strategy).toBe(strategy)
    }
  })

  it('rejects invalid strategy value', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', strategy: 'invalid' })).toThrow()
  })

  it('rejects wideRangePct below 0.01', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', wideRangePct: 0.001 })).toThrow()
  })

  it('rejects wideRangePct above 1.0', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', wideRangePct: 1.5 })).toThrow()
  })

  it('rejects volLookbackHours below 0.5', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', volLookbackHours: 0.1 })).toThrow()
  })

  it('rejects volLookbackHours above 24', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', volLookbackHours: 48 })).toThrow()
  })

  // --- New regime/vol fields ---

  it('applies defaults for new regime fields', () => {
    const result = PoolConfigSchema.parse({ poolId: '0xabc' })
    expect(result.volScalingMode).toBe('continuous')
    expect(result.sigmaLow).toBe(40)
    expect(result.sigmaHigh).toBe(120)
    expect(result.regimeEnabled).toBe(true)
    expect(result.binanceVolFallback).toBe(false)
  })

  it('accepts volScalingMode=tiered', () => {
    const result = PoolConfigSchema.parse({ poolId: '0xabc', volScalingMode: 'tiered' })
    expect(result.volScalingMode).toBe('tiered')
  })

  it('rejects invalid volScalingMode', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', volScalingMode: 'invalid' })).toThrow()
  })

  it('rejects sigmaLow below 1', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', sigmaLow: 0 })).toThrow()
  })

  it('rejects sigmaHigh above 500', () => {
    expect(() => PoolConfigSchema.parse({ poolId: '0xabc', sigmaHigh: 501 })).toThrow()
  })

  it('accepts custom sigma values', () => {
    const result = PoolConfigSchema.parse({ poolId: '0xabc', sigmaLow: 20, sigmaHigh: 200 })
    expect(result.sigmaLow).toBe(20)
    expect(result.sigmaHigh).toBe(200)
  })

  it('accepts regimeEnabled=false', () => {
    const result = PoolConfigSchema.parse({ poolId: '0xabc', regimeEnabled: false })
    expect(result.regimeEnabled).toBe(false)
  })

  it('accepts binanceVolFallback=true', () => {
    const result = PoolConfigSchema.parse({ poolId: '0xabc', binanceVolFallback: true })
    expect(result.binanceVolFallback).toBe(true)
  })
})

// ── ConfigSchema ────────────────────────────────────────────────────

describe('ConfigSchema', () => {
  const validMinimal = {
    privateKey: 'suiprivkey1abc123',
    pools: [{ poolId: '0xpool1' }],
  }

  it('accepts valid minimal config', () => {
    const result = ConfigSchema.parse(validMinimal)
    expect(result.privateKey).toBe('suiprivkey1abc123')
    expect(result.pools).toHaveLength(1)
  })

  it('applies all defaults correctly', () => {
    const result = ConfigSchema.parse(validMinimal)
    expect(result.network).toBe('testnet')
    expect(result.dryRun).toBe(true)
    expect(result.rebalanceThreshold).toBe(0.03)
    expect(result.harvestIntervalSec).toBe(7200)
    expect(result.checkIntervalSec).toBe(30)
    expect(result.slippageTolerance).toBe(0.01)
    expect(result.minGasProfitRatio).toBe(2)
    expect(result.logLevel).toBe('info')
    expect(result.harvestThresholdUsd).toBe(0.50)
    expect(result.swapFreeRebalance).toBe(true)
    expect(result.maxIdleSwapRatio).toBe(0.20)
  })

  it('accepts maxIdleSwapRatio in valid range', () => {
    expect(ConfigSchema.parse({ ...validMinimal, maxIdleSwapRatio: 0 }).maxIdleSwapRatio).toBe(0)
    expect(ConfigSchema.parse({ ...validMinimal, maxIdleSwapRatio: 0.5 }).maxIdleSwapRatio).toBe(0.5)
    expect(ConfigSchema.parse({ ...validMinimal, maxIdleSwapRatio: 1.0 }).maxIdleSwapRatio).toBe(1.0)
  })

  it('rejects maxIdleSwapRatio above 1', () => {
    expect(() => ConfigSchema.parse({ ...validMinimal, maxIdleSwapRatio: 1.5 })).toThrow()
  })

  it('rejects missing privateKey', () => {
    expect(() => ConfigSchema.parse({ pools: [{ poolId: '0xpool1' }] })).toThrow()
  })

  it('rejects empty privateKey', () => {
    expect(() => ConfigSchema.parse({ privateKey: '', pools: [{ poolId: '0xpool1' }] })).toThrow()
  })

  it('rejects empty pools array', () => {
    expect(() => ConfigSchema.parse({ privateKey: 'key123', pools: [] })).toThrow()
  })

  it('rejects rebalanceThreshold above 1', () => {
    expect(() => ConfigSchema.parse({ ...validMinimal, rebalanceThreshold: 1.5 })).toThrow()
  })

  it('rejects rebalanceThreshold below 0', () => {
    expect(() => ConfigSchema.parse({ ...validMinimal, rebalanceThreshold: -0.1 })).toThrow()
  })

  it('accepts rebalanceThreshold at boundary values', () => {
    expect(ConfigSchema.parse({ ...validMinimal, rebalanceThreshold: 0 }).rebalanceThreshold).toBe(0)
    expect(ConfigSchema.parse({ ...validMinimal, rebalanceThreshold: 1 }).rebalanceThreshold).toBe(1)
  })

  it('handles dryRun as boolean', () => {
    expect(ConfigSchema.parse({ ...validMinimal, dryRun: false }).dryRun).toBe(false)
    expect(ConfigSchema.parse({ ...validMinimal, dryRun: true }).dryRun).toBe(true)
  })

  it('accepts mainnet network', () => {
    expect(ConfigSchema.parse({ ...validMinimal, network: 'mainnet' }).network).toBe('mainnet')
  })

  it('rejects invalid network', () => {
    expect(() => ConfigSchema.parse({ ...validMinimal, network: 'devnet' })).toThrow()
  })

  it('rejects invalid logLevel', () => {
    expect(() => ConfigSchema.parse({ ...validMinimal, logLevel: 'verbose' })).toThrow()
  })

  it('rejects slippageTolerance above 0.5', () => {
    expect(() => ConfigSchema.parse({ ...validMinimal, slippageTolerance: 0.6 })).toThrow()
  })
})

// ── loadConfig() ────────────────────────────────────────────────────

describe('loadConfig', () => {
  const originalEnv = process.env

  beforeEach(() => {
    process.env = { ...originalEnv }
    vi.resetModules()
  })

  afterEach(() => {
    process.env = originalEnv
    vi.restoreAllMocks()
  })

  async function callLoadConfig() {
    // Mock dotenv to prevent it from reading real .env file
    vi.doMock('dotenv', () => ({ default: { config: vi.fn() } }))
    // Mock logger
    vi.doMock('../../src/utils/logger.js', () => ({
      getLogger: () => ({ info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() }),
    }))
    const { loadConfig } = await import('../../src/config/index.js')
    return loadConfig
  }

  it('parses POOL_IDS comma-separated correctly', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1, 0xpool2, 0xpool3'
    const config = loadConfig()
    expect(config.pools).toHaveLength(3)
    expect(config.pools[0].poolId).toBe('0xpool1')
    expect(config.pools[1].poolId).toBe('0xpool2')
    expect(config.pools[2].poolId).toBe('0xpool3')
  })

  it('handles POSITION_IDS as optional', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    delete process.env.POSITION_IDS
    const config = loadConfig()
    expect(config.pools[0].positionIds).toBeUndefined()
  })

  it('attaches POSITION_IDS to all pools when provided', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    process.env.POSITION_IDS = '0xpos1, 0xpos2'
    const config = loadConfig()
    expect(config.pools[0].positionIds).toEqual(['0xpos1', '0xpos2'])
  })

  it('DRY_RUN=false sets dryRun to false', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    process.env.DRY_RUN = 'false'
    const config = loadConfig()
    expect(config.dryRun).toBe(false)
  })

  it('DRY_RUN=true sets dryRun to true', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    process.env.DRY_RUN = 'true'
    const config = loadConfig()
    expect(config.dryRun).toBe(true)
  })

  it('missing DRY_RUN defaults to dryRun=true', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    delete process.env.DRY_RUN
    const config = loadConfig()
    expect(config.dryRun).toBe(true)
  })

  it('restores position IDs from state.json', async () => {
    vi.doMock('../../src/utils/state.js', () => ({
      loadState: () => ({
        version: 1,
        positions: { '0xpool1': '0xrestoredPos' },
        lastUpdated: '2025-01-01T00:00:00Z',
      }),
    }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    process.env.POSITION_IDS = '0xenvPos'
    const config = loadConfig()
    // state.json overrides env POSITION_IDS
    expect(config.pools[0].positionIds).toEqual(['0xrestoredPos'])
  })

  it('does not modify pools when state.json is null', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    process.env.POSITION_IDS = '0xenvPos'
    const config = loadConfig()
    expect(config.pools[0].positionIds).toEqual(['0xenvPos'])
  })

  it('maps SUI_NETWORK env var to config.network', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    process.env.SUI_NETWORK = 'mainnet'
    const config = loadConfig()
    expect(config.network).toBe('mainnet')
  })

  it('state.json with unmatched pool ID does not alter config', async () => {
    vi.doMock('../../src/utils/state.js', () => ({
      loadState: () => ({
        version: 1,
        positions: { '0xother_pool': '0xsomePos' },
        lastUpdated: '2025-01-01T00:00:00Z',
      }),
    }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    process.env.POSITION_IDS = '0xenvPos'
    const config = loadConfig()
    // State has a different pool, so env POSITION_IDS should remain
    expect(config.pools[0].positionIds).toEqual(['0xenvPos'])
  })

  it('SWAP_FREE_REBALANCE=false sets swapFreeRebalance to false', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    process.env.SWAP_FREE_REBALANCE = 'false'
    const config = loadConfig()
    expect(config.swapFreeRebalance).toBe(false)
  })

  it('missing SWAP_FREE_REBALANCE defaults to true', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    delete process.env.SWAP_FREE_REBALANCE
    const config = loadConfig()
    expect(config.swapFreeRebalance).toBe(true)
  })

  it('SWAP_FREE_REBALANCE=true sets swapFreeRebalance to true', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    process.env.SWAP_FREE_REBALANCE = 'true'
    const config = loadConfig()
    expect(config.swapFreeRebalance).toBe(true)
  })

  it('parses numeric env vars correctly', async () => {
    vi.doMock('../../src/utils/state.js', () => ({ loadState: () => null }))
    const loadConfig = await callLoadConfig()
    process.env.SUI_PRIVATE_KEY = 'testkey123'
    process.env.POOL_IDS = '0xpool1'
    process.env.REBALANCE_THRESHOLD = '0.20'
    process.env.CHECK_INTERVAL = '60'
    process.env.COMPOUND_INTERVAL = '3600'
    process.env.SLIPPAGE_TOLERANCE = '0.02'
    process.env.HARVEST_THRESHOLD_USD = '5.0'
    const config = loadConfig()
    expect(config.rebalanceThreshold).toBe(0.20)
    expect(config.checkIntervalSec).toBe(60)
    expect(config.harvestIntervalSec).toBe(3600)
    expect(config.slippageTolerance).toBe(0.02)
    expect(config.harvestThresholdUsd).toBe(5.0)
  })
})
