/**
 * Rebalance ROI Analysis Script (Issue #46)
 *
 * Analyzes each rebalance's cost vs. revenue contribution by correlating
 * rebalance_complete events with harvest/compound events in the subsequent epoch.
 *
 * Usage:
 *   npx tsx scripts/analyze-rebalance-roi.ts              # all dates
 *   npx tsx scripts/analyze-rebalance-roi.ts 2026-02-23   # specific date
 *   npx tsx scripts/analyze-rebalance-roi.ts --last 7     # last 7 days
 */
import { readEvents, listEventDates, type EventRecord } from '../src/utils/event-log.js'

interface RebalanceEpoch {
  rebalanceTime: string
  positionId: string
  trigger: string
  swapFree: boolean
  gasCostSui: number
  swapCostEstUsd: number  // estimated from swap amount × pool fee rate
  totalCostUsd: number
  epochDurationHours: number
  feesEarnedUsd: number
  roi: number  // feesEarned / totalCost
  inRangeRatio: number  // approximate (1.0 if no range-out before next rebalance)
}

function collectEpochs(events: EventRecord[]): RebalanceEpoch[] {
  const rebalances: EventRecord[] = []
  const harvests: EventRecord[] = []
  const idleDeploys: EventRecord[] = []
  const swaps: EventRecord[] = []

  for (const ev of events) {
    switch (ev.type) {
      case 'rebalance_complete':
        rebalances.push(ev)
        break
      case 'harvest_execute':
      case 'compound_execute':
        harvests.push(ev)
        break
      case 'idle_deploy_complete':
        idleDeploys.push(ev)
        break
      case 'rebalance_swap':
      case 'rebalance_ratio_swap':
        swaps.push(ev)
        break
    }
  }

  // Sort by time
  rebalances.sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  harvests.sort((a, b) => a.timestamp.localeCompare(b.timestamp))

  const epochs: RebalanceEpoch[] = []

  // Derive SUI price from rebalance_check events (currentPrice = coinB/coinA = SUI per USDC)
  // SUI price in USD = 1 / currentPrice
  const priceChecks = events.filter(e =>
    e.type === 'rebalance_check' && typeof e.data.currentPrice === 'number'
  )
  const latestPrice = priceChecks.length > 0
    ? (priceChecks[priceChecks.length - 1].data.currentPrice as number)
    : 1.05
  const SUI_PRICE_APPROX = 1 / latestPrice  // USD per SUI

  for (let i = 0; i < rebalances.length; i++) {
    const rb = rebalances[i]
    const nextRb = rebalances[i + 1]
    const rbTime = new Date(rb.timestamp).getTime()
    const nextTime = nextRb
      ? new Date(nextRb.timestamp).getTime()
      : Date.now()

    const epochDurationHours = (nextTime - rbTime) / (3600 * 1000)

    // Gas cost
    const gasSui = Number(BigInt(rb.data.totalGas as string || '0')) / 1e9
    const gasCostUsd = gasSui * SUI_PRICE_APPROX

    // Swap costs: find matching swaps near this rebalance time
    const rbSwaps = swaps.filter(s => {
      const st = new Date(s.timestamp).getTime()
      return st >= rbTime - 60_000 && st <= rbTime + 60_000
        && s.positionId === rb.positionId
    })
    let swapCostEstUsd = 0
    for (const s of rbSwaps) {
      const amount = Number(BigInt(s.data.amount as string || '0'))
      // Rough estimate: 0.25% of swap amount
      if (s.data.a2b) {
        // Swapping USDC to SUI
        swapCostEstUsd += (amount / 1e6) * 0.0025
      } else {
        // Swapping SUI to USDC
        swapCostEstUsd += (amount / 1e9) * SUI_PRICE_APPROX * 0.0025
      }
    }

    // Idle deploy swap cost
    const rbIdleDeploys = idleDeploys.filter(d => {
      const dt = new Date(d.timestamp).getTime()
      return dt >= rbTime && dt <= rbTime + 120_000
        && d.positionId === rb.positionId
    })
    for (const d of rbIdleDeploys) {
      const idleGas = Number(BigInt(d.data.gasCost as string || '0')) / 1e9
      gasCostUsd // already counted in totalGas? Check event structure
    }

    const totalCostUsd = gasCostUsd + swapCostEstUsd

    // Fees earned in this epoch (harvests between this rebalance and next)
    const epochHarvests = harvests.filter(h => {
      const ht = new Date(h.timestamp).getTime()
      return ht > rbTime && ht < nextTime
    })
    let feesEarnedUsd = 0
    for (const h of epochHarvests) {
      feesEarnedUsd += parseFloat(String(h.data.totalUsd ?? '0')) || 0
    }

    // In-range ratio: check if any range-out events occurred in this epoch
    const rangeOutEvents = events.filter(e => {
      if (e.type !== 'rebalance_check') return false
      const et = new Date(e.timestamp).getTime()
      return et > rbTime && et < nextTime
        && e.data.trigger === 'range-out'
        && e.data.shouldRebalance === true
    })
    const totalChecks = events.filter(e => {
      if (e.type !== 'rebalance_check') return false
      const et = new Date(e.timestamp).getTime()
      return et > rbTime && et < nextTime
    }).length
    const inRangeRatio = totalChecks > 0
      ? 1 - (rangeOutEvents.length / totalChecks)
      : 1.0

    const roi = totalCostUsd > 0 ? feesEarnedUsd / totalCostUsd : 0

    epochs.push({
      rebalanceTime: rb.timestamp,
      positionId: rb.positionId || 'unknown',
      trigger: rb.data.trigger as string || 'unknown',
      swapFree: rb.data.swapFree as boolean || false,
      gasCostSui: gasSui,
      swapCostEstUsd,
      totalCostUsd,
      epochDurationHours,
      feesEarnedUsd,
      roi,
      inRangeRatio,
    })
  }

  return epochs
}

function formatReport(epochs: RebalanceEpoch[]): string {
  const lines: string[] = []
  const hr = '─'.repeat(100)

  lines.push('')
  lines.push(hr)
  lines.push('  Rebalance ROI Analysis')
  lines.push(hr)
  lines.push('')

  if (epochs.length === 0) {
    lines.push('  No rebalance events found.')
    return lines.join('\n')
  }

  // Header
  lines.push('  Time                    Trigger      SwapFree  Gas(SUI)  SwapCost($)  TotalCost($)  Fees($)   ROI     Duration(h)  InRange')
  lines.push('  ' + '─'.repeat(96))

  for (const ep of epochs) {
    const shortTime = ep.rebalanceTime.slice(0, 19)
    const trigger = ep.trigger.padEnd(12)
    const swapFree = ep.swapFree ? 'Yes' : 'No '
    const gas = ep.gasCostSui.toFixed(4).padStart(8)
    const swap = ep.swapCostEstUsd.toFixed(4).padStart(11)
    const total = ep.totalCostUsd.toFixed(4).padStart(12)
    const fees = ep.feesEarnedUsd.toFixed(4).padStart(8)
    const roi = ep.roi === Infinity ? '   Inf' : (ep.roi * 100).toFixed(0).padStart(5) + '%'
    const duration = ep.epochDurationHours.toFixed(1).padStart(11)
    const inRange = (ep.inRangeRatio * 100).toFixed(0).padStart(4) + '%'
    lines.push(`  ${shortTime}  ${trigger}  ${swapFree}    ${gas}  ${swap}  ${total}  ${fees}  ${roi}  ${duration}  ${inRange}`)
  }

  lines.push('')

  // Summary
  const totalCost = epochs.reduce((s, e) => s + e.totalCostUsd, 0)
  const totalFees = epochs.reduce((s, e) => s + e.feesEarnedUsd, 0)
  const avgROI = totalCost > 0 ? totalFees / totalCost : 0
  const avgDuration = epochs.reduce((s, e) => s + e.epochDurationHours, 0) / epochs.length
  const unprofitable = epochs.filter(e => e.roi < 1).length

  lines.push('  ## Summary')
  lines.push(`  Total rebalances:     ${epochs.length}`)
  lines.push(`  Total cost:           $${totalCost.toFixed(4)}`)
  lines.push(`  Total fees earned:    $${totalFees.toFixed(4)}`)
  lines.push(`  Average ROI:          ${(avgROI * 100).toFixed(0)}%`)
  lines.push(`  Average epoch:        ${avgDuration.toFixed(1)}h`)
  lines.push(`  Unprofitable (ROI<1): ${unprofitable} / ${epochs.length}`)
  lines.push('')

  // Unprofitable patterns
  if (unprofitable > 0) {
    lines.push('  ## Unprofitable Rebalances')
    for (const ep of epochs.filter(e => e.roi < 1)) {
      lines.push(`  - ${ep.rebalanceTime.slice(0, 19)} trigger=${ep.trigger} cost=$${ep.totalCostUsd.toFixed(4)} fees=$${ep.feesEarnedUsd.toFixed(4)} duration=${ep.epochDurationHours.toFixed(1)}h`)
    }
    lines.push('')
  }

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

const epochs = collectEpochs(allEvents)
console.log(formatReport(epochs))
