import { getLogger } from './logger.js'

interface FeeSnapshot {
  feeA: bigint
  feeB: bigint
  timestamp: number // ms
}

interface HourlyFeeRate {
  /** Fee A accrued per hour (raw units, e.g. USDC with 6 decimals) */
  feeAPerHour: bigint
  /** Fee B accrued per hour (raw units, e.g. SUI with 9 decimals) */
  feeBPerHour: bigint
  /** Hours of observation data available */
  observationHours: number
}

/**
 * Tracks fee accrual over time per position.
 * Records snapshots of on-chain feeOwed values and computes
 * the actual hourly earning rate from deltas.
 *
 * Note: Fees reset to 0 when collected (harvest or close).
 * The tracker detects resets (fee decrease) and starts a new
 * observation window.
 */
class FeeTracker {
  // positionId → chronological snapshots (max 2 kept: first + latest)
  private snapshots = new Map<string, { first: FeeSnapshot; latest: FeeSnapshot }>()

  /**
   * Record a fee snapshot for a position.
   * Call this on every check cycle with the current on-chain feeOwed values.
   */
  record(positionId: string, feeA: bigint, feeB: bigint): void {
    const now = Date.now()
    const existing = this.snapshots.get(positionId)

    if (!existing) {
      // First observation
      const snap: FeeSnapshot = { feeA, feeB, timestamp: now }
      this.snapshots.set(positionId, { first: snap, latest: snap })
      return
    }

    // Detect fee reset (collected/harvested) — fees decreased
    if (feeA < existing.latest.feeA || feeB < existing.latest.feeB) {
      const log = getLogger()
      log.debug('Fee reset detected, starting new observation window', {
        positionId,
        prevA: existing.latest.feeA.toString(),
        prevB: existing.latest.feeB.toString(),
        newA: feeA.toString(),
        newB: feeB.toString(),
      })
      // Start fresh
      const snap: FeeSnapshot = { feeA, feeB, timestamp: now }
      this.snapshots.set(positionId, { first: snap, latest: snap })
      return
    }

    // Update latest
    existing.latest = { feeA, feeB, timestamp: now }
  }

  /**
   * Get the observed hourly fee accrual rate for a position.
   * Returns null if insufficient observation time (< 5 minutes).
   */
  getHourlyRate(positionId: string): HourlyFeeRate | null {
    const data = this.snapshots.get(positionId)
    if (!data) return null

    const elapsedMs = data.latest.timestamp - data.first.timestamp
    const MIN_OBSERVATION_MS = 5 * 60 * 1000 // 5 minutes minimum

    if (elapsedMs < MIN_OBSERVATION_MS) {
      return null // Not enough data yet
    }

    const deltaA = data.latest.feeA - data.first.feeA
    const deltaB = data.latest.feeB - data.first.feeB
    const elapsedHours = elapsedMs / (3600 * 1000)

    // Use integer division to preserve precision for large values
    const feeAPerHour = deltaA > 0n ? (deltaA * 3600_000n) / BigInt(elapsedMs) : 0n
    const feeBPerHour = deltaB > 0n ? (deltaB * 3600_000n) / BigInt(elapsedMs) : 0n

    return {
      feeAPerHour,
      feeBPerHour,
      observationHours: elapsedHours,
    }
  }

  /**
   * Remove tracking data for a position (e.g. after close).
   */
  remove(positionId: string): void {
    this.snapshots.delete(positionId)
  }

  /**
   * Transfer tracking from old position to new one (after rebalance).
   * Clears the old data since fee counters reset on the new position.
   */
  handleRebalance(oldPositionId: string, newPositionId?: string): void {
    this.snapshots.delete(oldPositionId)
    // New position starts fresh — no data to transfer since fees reset
  }
}

// Singleton instance
export const feeTracker = new FeeTracker()
