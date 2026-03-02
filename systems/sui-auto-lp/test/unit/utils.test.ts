import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'

// ── FeeTracker ──────────────────────────────────────────────────────────────

describe('FeeTracker', () => {
  // We need a fresh FeeTracker for each test. The module exports a singleton,
  // so we re-import by resetting modules.
  let feeTracker: typeof import('../../src/utils/fee-tracker.js')['feeTracker']

  beforeEach(async () => {
    vi.resetModules()
    const mod = await import('../../src/utils/fee-tracker.js')
    feeTracker = mod.feeTracker
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('record() first observation stores initial snapshot', () => {
    feeTracker.record('pos1', 100n, 200n)
    // Not enough time has passed, so hourly rate should be null
    expect(feeTracker.getHourlyRate('pos1')).toBeNull()
  })

  it('record() subsequent call updates latest but keeps first', () => {
    const t0 = 1_000_000
    const tenMin = 10 * 60 * 1000

    vi.spyOn(Date, 'now').mockReturnValue(t0)
    feeTracker.record('pos1', 100n, 200n)

    vi.spyOn(Date, 'now').mockReturnValue(t0 + tenMin)
    feeTracker.record('pos1', 200n, 400n)

    const rate = feeTracker.getHourlyRate('pos1')
    expect(rate).not.toBeNull()
    // deltaA = 100, elapsed = 10min => feeAPerHour = 100 * 3600000 / 600000 = 600
    expect(rate!.feeAPerHour).toBe(600n)
    // deltaB = 200 => feeBPerHour = 200 * 3600000 / 600000 = 1200
    expect(rate!.feeBPerHour).toBe(1200n)
  })

  it('record() fee decrease (reset) starts new observation window', () => {
    const t0 = 1_000_000
    const tenMin = 10 * 60 * 1000

    vi.spyOn(Date, 'now').mockReturnValue(t0)
    feeTracker.record('pos1', 100n, 200n)

    vi.spyOn(Date, 'now').mockReturnValue(t0 + tenMin)
    feeTracker.record('pos1', 200n, 400n)

    // Fee decreases (collected) — should reset window
    vi.spyOn(Date, 'now').mockReturnValue(t0 + tenMin + 1000)
    feeTracker.record('pos1', 10n, 20n)

    // After reset, not enough elapsed time
    expect(feeTracker.getHourlyRate('pos1')).toBeNull()
  })

  it('getHourlyRate() returns null with < 5min data', () => {
    const t0 = 1_000_000
    vi.spyOn(Date, 'now').mockReturnValue(t0)
    feeTracker.record('pos1', 100n, 200n)

    // Only 4 minutes later
    vi.spyOn(Date, 'now').mockReturnValue(t0 + 4 * 60 * 1000)
    feeTracker.record('pos1', 200n, 400n)

    expect(feeTracker.getHourlyRate('pos1')).toBeNull()
  })

  it('getHourlyRate() returns null for unknown position', () => {
    expect(feeTracker.getHourlyRate('unknown')).toBeNull()
  })

  it('getHourlyRate() calculates correctly after sufficient time', () => {
    const t0 = 1_000_000
    const oneHour = 3600 * 1000

    vi.spyOn(Date, 'now').mockReturnValue(t0)
    feeTracker.record('pos1', 0n, 0n)

    vi.spyOn(Date, 'now').mockReturnValue(t0 + oneHour)
    feeTracker.record('pos1', 1000n, 2000n)

    const rate = feeTracker.getHourlyRate('pos1')!
    expect(rate.feeAPerHour).toBe(1000n)
    expect(rate.feeBPerHour).toBe(2000n)
    expect(rate.observationHours).toBeCloseTo(1.0, 5)
  })

  it('remove() clears position data', () => {
    feeTracker.record('pos1', 100n, 200n)
    feeTracker.remove('pos1')
    expect(feeTracker.getHourlyRate('pos1')).toBeNull()
  })

  it('handleRebalance() clears old position', () => {
    feeTracker.record('old-pos', 100n, 200n)
    feeTracker.handleRebalance('old-pos', 'new-pos')
    expect(feeTracker.getHourlyRate('old-pos')).toBeNull()
    // New position has no data yet
    expect(feeTracker.getHourlyRate('new-pos')).toBeNull()
  })
})

// ── state.ts ────────────────────────────────────────────────────────────────

describe('state', () => {
  let tmpDir: string
  let loadState: typeof import('../../src/utils/state.js')['loadState']
  let savePositionId: typeof import('../../src/utils/state.js')['savePositionId']

  beforeEach(async () => {
    vi.resetModules()
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sui-auto-lp-state-'))
    // Mock process.cwd to point to our temp dir so STATE_PATH resolves there
    vi.spyOn(process, 'cwd').mockReturnValue(tmpDir)
    const mod = await import('../../src/utils/state.js')
    loadState = mod.loadState
    savePositionId = mod.savePositionId
  })

  afterEach(() => {
    vi.restoreAllMocks()
    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  it('loadState() returns null when file does not exist', () => {
    expect(loadState()).toBeNull()
  })

  it('loadState() returns null when version is wrong', () => {
    fs.writeFileSync(path.join(tmpDir, 'state.json'), JSON.stringify({ version: 99, positions: {} }))
    expect(loadState()).toBeNull()
  })

  it('loadState() returns null for corrupt JSON', () => {
    fs.writeFileSync(path.join(tmpDir, 'state.json'), '{{not valid json')
    expect(loadState()).toBeNull()
  })

  it('loadState() returns BotState for valid file', () => {
    const state = { version: 1, positions: { pool1: 'pos1' }, lastUpdated: '2025-01-01T00:00:00Z' }
    fs.writeFileSync(path.join(tmpDir, 'state.json'), JSON.stringify(state))
    const result = loadState()
    expect(result).not.toBeNull()
    expect(result!.version).toBe(1)
    expect(result!.positions.pool1).toBe('pos1')
  })

  it('savePositionId() creates new state file', () => {
    savePositionId('poolA', 'posA')
    const result = loadState()
    expect(result).not.toBeNull()
    expect(result!.positions.poolA).toBe('posA')
  })

  it('savePositionId() merges with existing state', () => {
    savePositionId('poolA', 'posA')
    savePositionId('poolB', 'posB')
    const result = loadState()
    expect(result).not.toBeNull()
    expect(result!.positions.poolA).toBe('posA')
    expect(result!.positions.poolB).toBe('posB')
  })
})

// ── event-log.ts ────────────────────────────────────────────────────────────

describe('event-log', () => {
  let tmpDir: string
  let recordEvent: typeof import('../../src/utils/event-log.js')['recordEvent']
  let readEvents: typeof import('../../src/utils/event-log.js')['readEvents']
  let listEventDates: typeof import('../../src/utils/event-log.js')['listEventDates']
  let closeEventLog: typeof import('../../src/utils/event-log.js')['closeEventLog']

  beforeEach(async () => {
    vi.resetModules()
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sui-auto-lp-events-'))
    // event-log resolves LOG_DIR from path.resolve('logs'), which uses cwd
    vi.spyOn(process, 'cwd').mockReturnValue(tmpDir)
    const mod = await import('../../src/utils/event-log.js')
    recordEvent = mod.recordEvent
    readEvents = mod.readEvents
    listEventDates = mod.listEventDates
    closeEventLog = mod.closeEventLog
  })

  afterEach(() => {
    closeEventLog()
    vi.restoreAllMocks()
    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  it('recordEvent() writes JSONL line and readEvents() reads it back', async () => {
    recordEvent('rebalance_check', { price: 1.5 }, 'pos1', 'pool1')
    // Close stream to flush
    closeEventLog()

    // Wait briefly for file system
    await new Promise(r => setTimeout(r, 50))

    const today = new Date().toISOString().slice(0, 10)
    const events = readEvents(today)
    expect(events.length).toBe(1)
    expect(events[0].type).toBe('rebalance_check')
    expect(events[0].data.price).toBe(1.5)
    expect(events[0].positionId).toBe('pos1')
    expect(events[0].poolId).toBe('pool1')
  })

  it('readEvents() for nonexistent date returns empty array', () => {
    expect(readEvents('1999-01-01')).toEqual([])
  })

  it('listEventDates() lists available dates', async () => {
    recordEvent('scheduler_start', {})
    closeEventLog()
    await new Promise(r => setTimeout(r, 50))

    const dates = listEventDates()
    const today = new Date().toISOString().slice(0, 10)
    expect(dates).toContain(today)
  })
})

// ── wallet.ts ───────────────────────────────────────────────────────────────

describe('wallet / loadKeypair', () => {
  let loadKeypair: typeof import('../../src/utils/wallet.js')['loadKeypair']

  beforeEach(async () => {
    vi.resetModules()
    const mod = await import('../../src/utils/wallet.js')
    loadKeypair = mod.loadKeypair
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('loads from base64 32-byte key', async () => {
    const { Ed25519Keypair } = await import('@mysten/sui/keypairs/ed25519')
    const { decodeSuiPrivateKey } = await import('@mysten/sui/cryptography')
    const original = Ed25519Keypair.generate()
    // Decode bech32 to get raw 32-byte secret key
    const { secretKey } = decodeSuiPrivateKey(original.getSecretKey())
    const b64 = Buffer.from(secretKey).toString('base64')

    const loaded = loadKeypair(b64)
    expect(loaded.getPublicKey().toSuiAddress()).toBe(original.getPublicKey().toSuiAddress())
  })

  it('loads from base64 33-byte key (with scheme prefix)', async () => {
    const { Ed25519Keypair } = await import('@mysten/sui/keypairs/ed25519')
    const { decodeSuiPrivateKey } = await import('@mysten/sui/cryptography')
    const original = Ed25519Keypair.generate()
    const { secretKey } = decodeSuiPrivateKey(original.getSecretKey())
    // Prepend scheme byte (0x00 for Ed25519)
    const withPrefix = Buffer.concat([Buffer.from([0x00]), Buffer.from(secretKey)])
    const b64 = withPrefix.toString('base64')

    const loaded = loadKeypair(b64)
    expect(loaded.getPublicKey().toSuiAddress()).toBe(original.getPublicKey().toSuiAddress())
  })

  it('loads from bech32 (suiprivkey) format', async () => {
    const { Ed25519Keypair } = await import('@mysten/sui/keypairs/ed25519')
    const original = Ed25519Keypair.generate()
    const bech32Key = original.getSecretKey()

    const loaded = loadKeypair(bech32Key)
    expect(loaded.getPublicKey().toSuiAddress()).toBe(original.getPublicKey().toSuiAddress())
  })
})

// ── logger.ts ───────────────────────────────────────────────────────────────

describe('logger', () => {
  let initLogger: typeof import('../../src/utils/logger.js')['initLogger']
  let getLogger: typeof import('../../src/utils/logger.js')['getLogger']

  beforeEach(async () => {
    vi.resetModules()
    const mod = await import('../../src/utils/logger.js')
    initLogger = mod.initLogger
    getLogger = mod.getLogger
  })

  it('initLogger() returns a winston logger', () => {
    const logger = initLogger('debug')
    expect(logger).toBeDefined()
    expect(typeof logger.info).toBe('function')
    expect(typeof logger.debug).toBe('function')
    expect(logger.level).toBe('debug')
  })

  it('getLogger() before init creates default info logger', () => {
    const logger = getLogger()
    expect(logger).toBeDefined()
    expect(logger.level).toBe('info')
  })

  it('getLogger() after init returns same instance', () => {
    const logger1 = initLogger('warn')
    const logger2 = getLogger()
    expect(logger2).toBe(logger1)
    expect(logger2.level).toBe('warn')
  })
})
