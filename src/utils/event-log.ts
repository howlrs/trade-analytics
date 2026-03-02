import fs from 'node:fs'
import path from 'node:path'
import { getLogger } from './logger.js'

export type EventType =
  | 'rebalance_check'
  | 'rebalance_triggered'
  | 'rebalance_close'
  | 'rebalance_swap'
  | 'rebalance_ratio_swap'
  | 'rebalance_open'
  | 'rebalance_complete'
  | 'rebalance_swap_fallback'
  | 'rebalance_error'
  | 'harvest_check'
  | 'harvest_execute'
  | 'harvest_skip'
  | 'harvest_error'
  | 'scheduler_start'
  | 'scheduler_stop'
  | 'scheduler_halt'
  | 'idle_deploy_complete'
  | 'idle_deploy_skip'
  | 'idle_deploy_error'

export interface EventRecord {
  timestamp: string
  type: EventType
  positionId?: string
  poolId?: string
  data: Record<string, unknown>
}

const LOG_DIR = path.resolve('logs')

let stream: fs.WriteStream | null = null
let currentDate: string | null = null
let streamError = false

function getDateStr(date: Date = new Date()): string {
  return date.toISOString().slice(0, 10)
}

function getLogPath(dateStr: string): string {
  return path.join(LOG_DIR, `events-${dateStr}.jsonl`)
}

function ensureStream(): fs.WriteStream {
  const today = getDateStr()
  if (stream && currentDate === today) return stream

  if (stream) stream.end()
  fs.mkdirSync(LOG_DIR, { recursive: true })
  currentDate = today
  streamError = false
  stream = fs.createWriteStream(getLogPath(today), { flags: 'a' })
  stream.on('error', (err) => {
    streamError = true
    try {
      getLogger().error('Event log stream error, falling back to stdout logging', { error: err.message })
    } catch {
      console.error('Event log stream error:', err.message)
    }
  })
  return stream
}

export function recordEvent(
  type: EventType,
  data: Record<string, unknown> = {},
  positionId?: string,
  poolId?: string,
): void {
  const record: EventRecord = {
    timestamp: new Date().toISOString(),
    type,
    positionId,
    poolId,
    data,
  }
  if (streamError) {
    try {
      getLogger().warn('Event log stream broken, logging event to stdout', { type, positionId, poolId })
    } catch {
      console.warn('Event log stream broken, event:', type)
    }
    return
  }
  ensureStream().write(JSON.stringify(record) + '\n')
}

export function readEvents(dateStr?: string): EventRecord[] {
  const target = dateStr ?? getDateStr()
  const logPath = getLogPath(target)
  if (!fs.existsSync(logPath)) return []

  return fs.readFileSync(logPath, 'utf-8')
    .split('\n')
    .filter(Boolean)
    .map(line => JSON.parse(line) as EventRecord)
}

export function listEventDates(): string[] {
  if (!fs.existsSync(LOG_DIR)) return []
  return fs.readdirSync(LOG_DIR)
    .filter(f => f.startsWith('events-') && f.endsWith('.jsonl'))
    .map(f => f.replace('events-', '').replace('.jsonl', ''))
    .sort()
}

export function closeEventLog(): void {
  if (stream) {
    stream.end()
    stream = null
    currentDate = null
  }
}
