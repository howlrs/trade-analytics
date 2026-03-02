import fs from 'node:fs'
import path from 'node:path'
import { getLogger } from './logger.js'

interface DailyRebalanceEntry {
  date: string   // YYYY-MM-DD (UTC)
  count: number
}

interface BotState {
  version: 1
  positions: Record<string, string>  // poolId → positionId
  lastRebalanceTimes?: Record<string, number>  // positionId → timestamp (ms)
  dailyRebalanceCounts?: Record<string, DailyRebalanceEntry>  // positionId → { date, count }
  lastUpdated: string
}

const STATE_PATH = path.resolve(process.cwd(), 'state.json')

/**
 * Load bot state from state.json. Returns null if file doesn't exist or is corrupt.
 */
export function loadState(): BotState | null {
  const log = getLogger()
  try {
    if (!fs.existsSync(STATE_PATH)) return null
    const raw = fs.readFileSync(STATE_PATH, 'utf-8')
    const parsed = JSON.parse(raw)
    if (parsed.version !== 1 || typeof parsed.positions !== 'object') {
      log.warn('state.json has unexpected format, ignoring', { parsed })
      return null
    }
    return parsed as BotState
  } catch (err) {
    log.warn('Failed to load state.json', {
      error: err instanceof Error ? err.message : String(err),
    })
    return null
  }
}

/**
 * Atomically write state to state.json (tmp + rename).
 */
function writeState(state: BotState): void {
  const tmpPath = STATE_PATH + '.tmp'
  fs.writeFileSync(tmpPath, JSON.stringify(state, null, 2) + '\n')
  fs.renameSync(tmpPath, STATE_PATH)
}

/**
 * Save a position ID mapping to state.json (atomic write via tmp + rename).
 */
export function savePositionId(poolId: string, positionId: string): void {
  const log = getLogger()
  try {
    const existing = loadState()
    const state: BotState = {
      version: 1,
      positions: existing?.positions ?? {},
      lastRebalanceTimes: existing?.lastRebalanceTimes,
      dailyRebalanceCounts: existing?.dailyRebalanceCounts,
      lastUpdated: new Date().toISOString(),
    }
    state.positions[poolId] = positionId
    writeState(state)
    log.info('Position ID persisted to state.json', { poolId, positionId })
  } catch (err) {
    log.error('Failed to save state.json', {
      error: err instanceof Error ? err.message : String(err),
    })
  }
}

/**
 * Load persisted lastRebalanceTimes from state.json.
 */
export function loadRebalanceTimes(): Record<string, number> {
  const state = loadState()
  return state?.lastRebalanceTimes ?? {}
}

/**
 * Save a rebalance timestamp to state.json.
 */
export function saveRebalanceTime(positionId: string, timestampMs: number): void {
  const log = getLogger()
  try {
    const existing = loadState()
    const state: BotState = {
      version: 1,
      positions: existing?.positions ?? {},
      lastRebalanceTimes: existing?.lastRebalanceTimes ?? {},
      dailyRebalanceCounts: existing?.dailyRebalanceCounts,
      lastUpdated: new Date().toISOString(),
    }
    state.lastRebalanceTimes![positionId] = timestampMs
    writeState(state)
    log.debug('Rebalance time persisted to state.json', { positionId, timestampMs })
  } catch (err) {
    log.error('Failed to save rebalance time to state.json', {
      error: err instanceof Error ? err.message : String(err),
    })
  }
}

/**
 * Load persisted daily rebalance counts from state.json.
 */
export function loadDailyRebalanceCounts(): Record<string, DailyRebalanceEntry> {
  const state = loadState()
  return state?.dailyRebalanceCounts ?? {}
}

/**
 * Save daily rebalance count for a position to state.json.
 */
export function saveDailyRebalanceCount(positionId: string, date: string, count: number): void {
  const log = getLogger()
  try {
    const existing = loadState()
    const state: BotState = {
      version: 1,
      positions: existing?.positions ?? {},
      lastRebalanceTimes: existing?.lastRebalanceTimes,
      dailyRebalanceCounts: existing?.dailyRebalanceCounts ?? {},
      lastUpdated: new Date().toISOString(),
    }
    state.dailyRebalanceCounts![positionId] = { date, count }
    writeState(state)
    log.debug('Daily rebalance count persisted to state.json', { positionId, date, count })
  } catch (err) {
    log.error('Failed to save daily rebalance count to state.json', {
      error: err instanceof Error ? err.message : String(err),
    })
  }
}

