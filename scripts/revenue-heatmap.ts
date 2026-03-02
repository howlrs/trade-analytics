/**
 * Revenue Heatmap Script (Issue #48)
 *
 * Generates a day-of-week × time-of-day (2h buckets) heatmap of fee accrual rates.
 * Used to identify peak/off-peak periods for potential Dynamic Compound Interval.
 *
 * Usage:
 *   npx tsx scripts/revenue-heatmap.ts              # all dates
 *   npx tsx scripts/revenue-heatmap.ts --last 14    # last 14 days
 */
import { readEvents, listEventDates, type EventRecord } from '../src/utils/event-log.js'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const HOURS_PER_BUCKET = 2
const BUCKETS = 24 / HOURS_PER_BUCKET  // 12 buckets

interface HeatmapCell {
  totalUsd: number
  count: number
  avgRate: number  // $/hour
}

type Heatmap = HeatmapCell[][]  // [dayOfWeek 0-6][bucket 0-11]

function buildHeatmap(events: EventRecord[]): Heatmap {
  // Initialize 7×12 grid
  const heatmap: Heatmap = Array.from({ length: 7 }, () =>
    Array.from({ length: BUCKETS }, () => ({ totalUsd: 0, count: 0, avgRate: 0 }))
  )

  // Find harvest_execute and compound_execute events with totalUsd
  const harvests = events.filter(e =>
    (e.type === 'harvest_execute' || e.type === 'compound_execute')
    && typeof e.data.totalUsd === 'number'
    && (e.data.totalUsd as number) > 0
  )

  // Also extract harvest_check/harvest_skip with fee info for better resolution
  const harvestChecks = events.filter(e =>
    e.type === 'rebalance_check'
    && typeof e.data.currentPrice === 'number'
  )

  for (const h of harvests) {
    const date = new Date(h.timestamp)
    // getUTCDay: 0=Sun, we want 0=Mon
    const dayIdx = (date.getUTCDay() + 6) % 7
    const hour = date.getUTCHours()
    const bucket = Math.floor(hour / HOURS_PER_BUCKET)

    const totalUsd = h.data.totalUsd as number
    heatmap[dayIdx][bucket].totalUsd += totalUsd
    heatmap[dayIdx][bucket].count++
  }

  // Calculate average hourly rate per cell
  for (let d = 0; d < 7; d++) {
    for (let b = 0; b < BUCKETS; b++) {
      const cell = heatmap[d][b]
      if (cell.count > 0) {
        // Each harvest covers roughly the compound interval (2h default)
        // So rate ≈ totalUsd / (count × HOURS_PER_BUCKET)
        cell.avgRate = cell.totalUsd / (cell.count * HOURS_PER_BUCKET)
      }
    }
  }

  return heatmap
}

function formatHeatmap(heatmap: Heatmap, totalDays: number): string {
  const lines: string[] = []
  const hr = '─'.repeat(90)

  lines.push('')
  lines.push(hr)
  lines.push(`  Fee Revenue Heatmap (${totalDays} days of data)`)
  lines.push('  Values: avg $/hour per bucket (UTC)')
  lines.push(hr)
  lines.push('')

  // Header: time buckets
  let header = '       '
  for (let b = 0; b < BUCKETS; b++) {
    const startH = (b * HOURS_PER_BUCKET).toString().padStart(2, '0')
    const endH = ((b + 1) * HOURS_PER_BUCKET).toString().padStart(2, '0')
    header += ` ${startH}-${endH} `
  }
  lines.push(header)
  lines.push('  ' + '─'.repeat(84))

  // Find max rate for scaling
  let maxRate = 0
  for (let d = 0; d < 7; d++) {
    for (let b = 0; b < BUCKETS; b++) {
      maxRate = Math.max(maxRate, heatmap[d][b].avgRate)
    }
  }

  // Heat levels for ASCII visualization
  const heatChars = [' ', '░', '▒', '▓', '█']

  for (let d = 0; d < 7; d++) {
    let row = `  ${DAYS[d]}  `
    let valRow = '       '
    for (let b = 0; b < BUCKETS; b++) {
      const cell = heatmap[d][b]
      const rate = cell.avgRate
      const level = maxRate > 0
        ? Math.min(heatChars.length - 1, Math.floor((rate / maxRate) * (heatChars.length - 1) + 0.5))
        : 0
      const heat = heatChars[level]
      row += ` ${heat}${heat}${heat}${heat}${heat} `
      valRow += rate > 0 ? ` $${rate.toFixed(2)} `.padEnd(7) : '   -   '
    }
    lines.push(row)
    lines.push(valRow)
  }

  lines.push('')

  // Per-session summary
  const sessions = [
    { name: 'Asia (00-08 UTC)', buckets: [0, 1, 2, 3] },
    { name: 'Europe (08-16 UTC)', buckets: [4, 5, 6, 7] },
    { name: 'Americas (16-24 UTC)', buckets: [8, 9, 10, 11] },
  ]

  lines.push('  ## Session Summary (avg $/hour)')
  for (const session of sessions) {
    let totalRate = 0
    let totalCount = 0
    for (const b of session.buckets) {
      for (let d = 0; d < 7; d++) {
        if (heatmap[d][b].count > 0) {
          totalRate += heatmap[d][b].avgRate
          totalCount++
        }
      }
    }
    const avgRate = totalCount > 0 ? totalRate / totalCount : 0
    lines.push(`  ${session.name.padEnd(25)} $${avgRate.toFixed(4)}/h`)
  }
  lines.push('')

  // Weekday vs Weekend
  let weekdayRate = 0, weekdayCount = 0
  let weekendRate = 0, weekendCount = 0
  for (let d = 0; d < 7; d++) {
    for (let b = 0; b < BUCKETS; b++) {
      if (heatmap[d][b].count > 0) {
        if (d < 5) {
          weekdayRate += heatmap[d][b].avgRate
          weekdayCount++
        } else {
          weekendRate += heatmap[d][b].avgRate
          weekendCount++
        }
      }
    }
  }
  lines.push('  ## Weekday vs Weekend')
  lines.push(`  Weekday avg:  $${weekdayCount > 0 ? (weekdayRate / weekdayCount).toFixed(4) : '0.0000'}/h`)
  lines.push(`  Weekend avg:  $${weekendCount > 0 ? (weekendRate / weekendCount).toFixed(4) : '0.0000'}/h`)
  lines.push('')

  // Dynamic Compound Interval recommendation
  if (maxRate > 0) {
    const overallAvg = [...Array(7)].reduce((sum, _, d) =>
      sum + [...Array(BUCKETS)].reduce((s, __, b) =>
        s + (heatmap[d][b].count > 0 ? heatmap[d][b].avgRate : 0), 0
      ), 0) / Math.max(1, [...Array(7)].reduce((cnt, _, d) =>
        cnt + [...Array(BUCKETS)].reduce((c, __, b) =>
          c + (heatmap[d][b].count > 0 ? 1 : 0), 0
        ), 0))

    const peakBuckets: string[] = []
    const offPeakBuckets: string[] = []

    for (let b = 0; b < BUCKETS; b++) {
      let bucketAvg = 0, bucketCnt = 0
      for (let d = 0; d < 7; d++) {
        if (heatmap[d][b].count > 0) {
          bucketAvg += heatmap[d][b].avgRate
          bucketCnt++
        }
      }
      if (bucketCnt > 0) {
        bucketAvg /= bucketCnt
        const startH = (b * HOURS_PER_BUCKET).toString().padStart(2, '0')
        const endH = ((b + 1) * HOURS_PER_BUCKET).toString().padStart(2, '0')
        if (bucketAvg > overallAvg * 1.3) {
          peakBuckets.push(`${startH}-${endH}`)
        } else if (bucketAvg < overallAvg * 0.7) {
          offPeakBuckets.push(`${startH}-${endH}`)
        }
      }
    }

    lines.push('  ## Dynamic Compound Interval Recommendation')
    if (peakBuckets.length > 0 || offPeakBuckets.length > 0) {
      lines.push(`  Peak hours (>30% above avg):     ${peakBuckets.join(', ') || 'none'}`)
      lines.push(`  Off-peak hours (<30% below avg): ${offPeakBuckets.join(', ') || 'none'}`)
      lines.push('  Suggested: peakHoursInterval=1h, offPeakInterval=4h')
    } else {
      lines.push('  Fee distribution is relatively uniform across time periods.')
      lines.push('  Dynamic compound interval may not provide significant benefit.')
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
  const days = parseInt(args[args.indexOf('--last') + 1] || '14', 10)
  const allDates = listEventDates()
  dates = allDates.slice(-days)
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

const heatmap = buildHeatmap(allEvents)
console.log(formatHeatmap(heatmap, dates.length))
