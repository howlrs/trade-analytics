/**
 * Daily Report Generator
 * Reads event logs and generates a summary report.
 *
 * Usage:
 *   npx tsx scripts/daily-report.ts              # today
 *   npx tsx scripts/daily-report.ts 2026-02-15   # specific date
 *   npx tsx scripts/daily-report.ts --all         # all dates
 */
import { readEvents, listEventDates, type EventRecord } from '../src/utils/event-log.js'

const DECIMALS_A = 6 // USDC
const DECIMALS_B = 9 // SUI

interface DailyStats {
  date: string
  totalChecks: number
  rebalancesTriggered: number
  rebalancesCompleted: number
  rebalanceErrors: number
  swapsExecuted: number
  compoundChecks: number
  compoundsExecuted: number
  compoundSkips: number
  compoundErrors: number
  totalGasUsed: bigint
  schedulerStarts: number
  schedulerHalts: number
  priceRange: { min: number; max: number } | null
  positions: Map<string, PositionStats>
}

interface PositionStats {
  checks: number
  rebalances: number
  compounds: number
  errors: number
  gasUsed: bigint
  lastPrice: number | null
}

function buildStats(events: EventRecord[], date: string): DailyStats {
  const stats: DailyStats = {
    date,
    totalChecks: 0,
    rebalancesTriggered: 0,
    rebalancesCompleted: 0,
    rebalanceErrors: 0,
    swapsExecuted: 0,
    compoundChecks: 0,
    compoundsExecuted: 0,
    compoundSkips: 0,
    compoundErrors: 0,
    totalGasUsed: 0n,
    schedulerStarts: 0,
    schedulerHalts: 0,
    priceRange: null,
    positions: new Map(),
  }

  function getPos(id: string): PositionStats {
    if (!stats.positions.has(id)) {
      stats.positions.set(id, { checks: 0, rebalances: 0, compounds: 0, errors: 0, gasUsed: 0n, lastPrice: null })
    }
    return stats.positions.get(id)!
  }

  for (const ev of events) {
    const pos = ev.positionId ? getPos(ev.positionId) : null

    switch (ev.type) {
      case 'rebalance_check': {
        stats.totalChecks++
        if (pos) {
          pos.checks++
          const price = ev.data.currentPrice as number | undefined
          if (price != null) {
            pos.lastPrice = price
            if (!stats.priceRange) {
              stats.priceRange = { min: price, max: price }
            } else {
              stats.priceRange.min = Math.min(stats.priceRange.min, price)
              stats.priceRange.max = Math.max(stats.priceRange.max, price)
            }
          }
        }
        break
      }
      case 'rebalance_triggered':
        stats.rebalancesTriggered++
        break
      case 'rebalance_swap':
        stats.swapsExecuted++
        if (ev.data.gasCost) stats.totalGasUsed += BigInt(ev.data.gasCost as string)
        break
      case 'rebalance_close':
        if (ev.data.gasCost) stats.totalGasUsed += BigInt(ev.data.gasCost as string)
        break
      case 'rebalance_complete':
        stats.rebalancesCompleted++
        if (pos) pos.rebalances++
        if (ev.data.totalGas) stats.totalGasUsed += BigInt(ev.data.totalGas as string)
        break
      case 'rebalance_error':
        stats.rebalanceErrors++
        if (pos) pos.errors++
        break
      case 'compound_check':
        stats.compoundChecks++
        break
      case 'compound_skip':
        stats.compoundSkips++
        break
      case 'compound_execute':
        stats.compoundsExecuted++
        if (pos) pos.compounds++
        if (ev.data.gasCost) stats.totalGasUsed += BigInt(ev.data.gasCost as string)
        break
      case 'compound_error':
        stats.compoundErrors++
        if (pos) pos.errors++
        break
      case 'scheduler_start':
        stats.schedulerStarts++
        break
      case 'scheduler_halt':
        stats.schedulerHalts++
        break
    }
  }

  return stats
}

function formatReport(stats: DailyStats): string {
  const lines: string[] = []
  const hr = '─'.repeat(60)

  lines.push('')
  lines.push(hr)
  lines.push(`  Daily Report: ${stats.date}`)
  lines.push(hr)
  lines.push('')

  // Overview
  lines.push('  ## Overview')
  lines.push(`  Scheduler starts:     ${stats.schedulerStarts}`)
  lines.push(`  Scheduler halts:      ${stats.schedulerHalts}`)
  lines.push(`  Total checks:         ${stats.totalChecks}`)
  lines.push('')

  // Price
  if (stats.priceRange) {
    lines.push('  ## SUI Price (observed)')
    lines.push(`  Min: $${stats.priceRange.min.toFixed(4)}`)
    lines.push(`  Max: $${stats.priceRange.max.toFixed(4)}`)
    const spread = stats.priceRange.max - stats.priceRange.min
    const spreadPct = (spread / stats.priceRange.min * 100).toFixed(2)
    lines.push(`  Spread: $${spread.toFixed(4)} (${spreadPct}%)`)
    lines.push('')
  }

  // Rebalance
  lines.push('  ## Rebalance')
  lines.push(`  Triggered:    ${stats.rebalancesTriggered}`)
  lines.push(`  Completed:    ${stats.rebalancesCompleted}`)
  lines.push(`  Swaps:        ${stats.swapsExecuted}`)
  lines.push(`  Errors:       ${stats.rebalanceErrors}`)
  lines.push('')

  // Compound
  lines.push('  ## Compound')
  lines.push(`  Checked:      ${stats.compoundChecks + stats.compoundSkips}`)
  lines.push(`  Executed:     ${stats.compoundsExecuted}`)
  lines.push(`  Skipped:      ${stats.compoundSkips}`)
  lines.push(`  Errors:       ${stats.compoundErrors}`)
  lines.push('')

  // Gas
  const gasHuman = Number(stats.totalGasUsed) / 1e9
  lines.push('  ## Gas Usage')
  lines.push(`  Total:        ${gasHuman.toFixed(6)} SUI`)
  lines.push('')

  // Per-position
  if (stats.positions.size > 0) {
    lines.push('  ## Positions')
    for (const [id, pos] of stats.positions) {
      const shortId = `${id.slice(0, 10)}...${id.slice(-6)}`
      lines.push(`  ${shortId}`)
      lines.push(`    Checks: ${pos.checks}  Rebalances: ${pos.rebalances}  Compounds: ${pos.compounds}  Errors: ${pos.errors}`)
      if (pos.lastPrice != null) {
        lines.push(`    Last price: $${pos.lastPrice.toFixed(4)}`)
      }
      lines.push(`    Gas used: ${(Number(pos.gasUsed) / 1e9).toFixed(6)} SUI`)
    }
    lines.push('')
  }

  // Health
  lines.push('  ## Health')
  if (stats.schedulerHalts > 0) {
    lines.push('  ⚠ SCHEDULER HALTED — manual review required')
  } else if (stats.rebalanceErrors > 0 || stats.compoundErrors > 0) {
    lines.push('  ⚠ Errors occurred — check logs for details')
  } else if (stats.totalChecks === 0) {
    lines.push('  - No activity recorded')
  } else {
    lines.push('  OK — no errors')
  }
  lines.push('')
  lines.push(hr)
  lines.push('')

  return lines.join('\n')
}

// --- Main ---
const args = process.argv.slice(2)

if (args.includes('--all')) {
  const dates = listEventDates()
  if (dates.length === 0) {
    console.log('No event logs found. Start the bot first to generate events.')
    process.exit(0)
  }
  for (const date of dates) {
    const events = readEvents(date)
    const stats = buildStats(events, date)
    console.log(formatReport(stats))
  }
} else {
  const date = args[0] ?? new Date().toISOString().slice(0, 10)
  const events = readEvents(date)
  if (events.length === 0) {
    console.log(`No events found for ${date}.`)
    console.log(`Available dates: ${listEventDates().join(', ') || '(none)'}`)
    process.exit(0)
  }
  const stats = buildStats(events, date)
  console.log(formatReport(stats))
}
