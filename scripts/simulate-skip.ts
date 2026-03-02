/**
 * Counter-factual Rebalance Skip Simulation (Issue #47)
 *
 * For each threshold-triggered rebalance, simulates what would have happened
 * if the rebalance had been skipped:
 *   - Would the position have gone out of range?
 *   - How long until range-out (OOR time)?
 *   - What was the opportunity cost vs. the saved rebalance cost?
 *
 * Usage:
 *   npx tsx scripts/simulate-skip.ts              # all dates
 *   npx tsx scripts/simulate-skip.ts 2026-02-23   # specific date
 *   npx tsx scripts/simulate-skip.ts --last 7     # last 7 days
 */
import { readEvents, listEventDates, type EventRecord } from '../src/utils/event-log.js'

interface ThresholdRebalance {
  timestamp: string
  positionId: string
  currentPrice: number
  rangeLower: number
  rangeUpper: number
  trigger: string
  gasCostSui: number
  swapFree: boolean
}

interface SkipSimulation {
  rebalance: ThresholdRebalance
  // Post-rebalance price trajectory (sampled from subsequent check events)
  priceAfter: Array<{ time: string; price: number }>
  // Counter-factual analysis
  wouldHaveGoneOOR: boolean
  timeToOOR_hours: number | null  // null = never went OOR within observation window
  oorDirection: 'up' | 'down' | null
  maxDeviation: number  // max distance from range center
  verdict: 'skip-safe' | 'skip-risky' | 'skip-costly'
}

function findThresholdRebalances(events: EventRecord[]): ThresholdRebalance[] {
  const results: ThresholdRebalance[] = []

  // Find rebalance_complete events triggered by 'threshold'
  const completes = events.filter(e =>
    e.type === 'rebalance_complete' && e.data.trigger === 'threshold'
  )

  // For each complete, find the corresponding rebalance_check
  for (const complete of completes) {
    const rbTime = new Date(complete.timestamp).getTime()

    // Find the most recent check before this complete for the same position
    const check = events
      .filter(e =>
        e.type === 'rebalance_check'
        && e.positionId === complete.positionId
        && new Date(e.timestamp).getTime() <= rbTime
        && e.data.shouldRebalance === true
        && e.data.trigger === 'threshold'
      )
      .sort((a, b) => b.timestamp.localeCompare(a.timestamp))[0]

    if (!check) continue

    results.push({
      timestamp: complete.timestamp,
      positionId: complete.positionId || 'unknown',
      currentPrice: check.data.currentPrice as number,
      rangeLower: check.data.currentLower as number,
      rangeUpper: check.data.currentUpper as number,
      trigger: 'threshold',
      gasCostSui: Number(BigInt(complete.data.totalGas as string || '0')) / 1e9,
      swapFree: complete.data.swapFree as boolean || false,
    })
  }

  return results
}

function simulateSkips(
  thresholdRebalances: ThresholdRebalance[],
  allEvents: EventRecord[],
): SkipSimulation[] {
  const simulations: SkipSimulation[] = []
  const allChecks = allEvents
    .filter(e => e.type === 'rebalance_check')
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp))

  for (const rb of thresholdRebalances) {
    const rbTime = new Date(rb.timestamp).getTime()
    const oldLower = rb.rangeLower
    const oldUpper = rb.rangeUpper

    // Collect price readings AFTER this rebalance, until next rebalance or end of data
    // Use the OLD range bounds to check if price would have gone OOR
    const nextRebalance = allEvents.find(e =>
      e.type === 'rebalance_complete'
      && new Date(e.timestamp).getTime() > rbTime
    )
    const endTime = nextRebalance
      ? new Date(nextRebalance.timestamp).getTime()
      : Date.now()

    const priceAfter: Array<{ time: string; price: number }> = []
    let wouldHaveGoneOOR = false
    let timeToOOR_hours: number | null = null
    let oorDirection: 'up' | 'down' | null = null
    let maxDeviation = 0

    const rangeCenter = (oldUpper + oldLower) / 2
    const rangeWidth = oldUpper - oldLower

    for (const check of allChecks) {
      const checkTime = new Date(check.timestamp).getTime()
      if (checkTime <= rbTime) continue
      if (checkTime > endTime) break

      const price = check.data.currentPrice as number
      if (price == null) continue

      priceAfter.push({ time: check.timestamp, price })

      const deviation = Math.abs(price - rangeCenter) / (rangeWidth / 2)
      maxDeviation = Math.max(maxDeviation, deviation)

      if (!wouldHaveGoneOOR) {
        if (price <= oldLower) {
          wouldHaveGoneOOR = true
          timeToOOR_hours = (checkTime - rbTime) / (3600 * 1000)
          oorDirection = 'down'
        } else if (price >= oldUpper) {
          wouldHaveGoneOOR = true
          timeToOOR_hours = (checkTime - rbTime) / (3600 * 1000)
          oorDirection = 'up'
        }
      }
    }

    // Determine verdict
    let verdict: 'skip-safe' | 'skip-risky' | 'skip-costly'
    if (!wouldHaveGoneOOR) {
      verdict = 'skip-safe'  // Could have skipped entirely
    } else if (timeToOOR_hours != null && timeToOOR_hours < 2) {
      verdict = 'skip-costly'  // Would have gone OOR very quickly
    } else {
      verdict = 'skip-risky'  // Eventually went OOR but not immediately
    }

    simulations.push({
      rebalance: rb,
      priceAfter,
      wouldHaveGoneOOR,
      timeToOOR_hours,
      oorDirection,
      maxDeviation,
      verdict,
    })
  }

  return simulations
}

function formatReport(simulations: SkipSimulation[]): string {
  const lines: string[] = []
  const hr = '─'.repeat(100)

  lines.push('')
  lines.push(hr)
  lines.push('  Counter-factual Skip Simulation (Threshold Rebalances)')
  lines.push(hr)
  lines.push('')

  if (simulations.length === 0) {
    lines.push('  No threshold-triggered rebalances found in the event logs.')
    lines.push('  This is expected if all rebalances were range-out triggered.')
    return lines.join('\n')
  }

  lines.push('  Time                    Price    Range             OOR?    Time-to-OOR  MaxDev  Verdict       GasCost(SUI)')
  lines.push('  ' + '─'.repeat(96))

  for (const sim of simulations) {
    const rb = sim.rebalance
    const time = rb.timestamp.slice(0, 19)
    const price = rb.currentPrice.toFixed(6).padStart(9)
    const range = `[${rb.rangeLower.toFixed(4)}-${rb.rangeUpper.toFixed(4)}]`
    const oor = sim.wouldHaveGoneOOR ? `Yes(${sim.oorDirection})` : 'No     '
    const tto = sim.timeToOOR_hours != null ? `${sim.timeToOOR_hours.toFixed(1)}h` : 'n/a   '
    const maxDev = (sim.maxDeviation * 100).toFixed(0).padStart(4) + '%'
    const verdict = sim.verdict.padEnd(12)
    const gas = rb.gasCostSui.toFixed(4)

    lines.push(`  ${time}  ${price}  ${range}  ${oor}  ${tto.padStart(11)}  ${maxDev}  ${verdict}  ${gas}`)
  }

  lines.push('')

  // Summary
  const skipSafe = simulations.filter(s => s.verdict === 'skip-safe').length
  const skipRisky = simulations.filter(s => s.verdict === 'skip-risky').length
  const skipCostly = simulations.filter(s => s.verdict === 'skip-costly').length
  const totalGasSaved = simulations
    .filter(s => s.verdict === 'skip-safe')
    .reduce((sum, s) => sum + s.rebalance.gasCostSui, 0)

  lines.push('  ## Summary')
  lines.push(`  Total threshold rebalances:  ${simulations.length}`)
  lines.push(`  Skip-safe (no OOR):          ${skipSafe}  (${(skipSafe / simulations.length * 100).toFixed(0)}%)`)
  lines.push(`  Skip-risky (eventual OOR):   ${skipRisky}  (${(skipRisky / simulations.length * 100).toFixed(0)}%)`)
  lines.push(`  Skip-costly (quick OOR):     ${skipCostly}  (${(skipCostly / simulations.length * 100).toFixed(0)}%)`)
  lines.push(`  Gas saved if skipped safe:   ${totalGasSaved.toFixed(4)} SUI`)
  lines.push('')

  // Recommendation
  lines.push('  ## Recommendation')
  if (skipSafe === simulations.length) {
    lines.push('  All threshold rebalances could have been safely skipped.')
    lines.push('  Consider disabling threshold trigger or raising REBALANCE_THRESHOLD further.')
  } else if (skipSafe + skipRisky === simulations.length) {
    lines.push('  No threshold rebalance was immediately costly to skip.')
    lines.push('  The current threshold may be too aggressive.')
  } else {
    lines.push(`  ${skipCostly} rebalances would have quickly gone OOR if skipped.`)
    lines.push('  Current threshold setting appears justified for those cases.')
  }
  lines.push('')
  lines.push(hr)

  return lines.join('\n')
}

// --- Main ---
const args = process.argv.slice(2)

let dates: string[]
if (args.includes('--last')) {
  const days = parseInt(args[args.indexOf('--last') + 1] || '7', 10)
  const allDates = listEventDates()
  dates = allDates.slice(-days)
} else if (args[0]) {
  dates = [args[0]]
} else {
  dates = listEventDates()
}

if (dates.length === 0) {
  console.log('No event logs found. Start the bot first to generate events.')
  process.exit(0)
}

const allEvents: EventRecord[] = []
for (const date of dates) {
  allEvents.push(...readEvents(date))
}

const thresholds = findThresholdRebalances(allEvents)
const simulations = simulateSkips(thresholds, allEvents)
console.log(formatReport(simulations))
