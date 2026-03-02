/**
 * Regime detection module.
 *
 * Pure functions that classify the current volatility regime based on
 * a rolling sigma history. The scheduler owns the history array and
 * passes it here each cycle.
 */

export interface RegimeState {
  regime: 'low' | 'mid' | 'high'
  /** sigma < percentile(20) of history — vol compression */
  isCompression: boolean
  /** sigma > MA + 1.5*std — regime transition signal */
  isTransition: boolean
  currentSigma: number
}

/**
 * Detect the current volatility regime from a sigma history.
 *
 * - History < 3 entries → safe default ('mid', no flags)
 * - regime: sigma relative to MA ± 0.5*std → low / mid / high
 * - isCompression: current sigma ≤ 20th percentile of history
 * - isTransition: current sigma > MA + 1.5*std
 */
export function detectRegime(sigmaHistory: readonly number[]): RegimeState {
  if (sigmaHistory.length < 3) {
    return {
      regime: 'mid',
      isCompression: false,
      isTransition: false,
      currentSigma: sigmaHistory.length > 0 ? sigmaHistory[sigmaHistory.length - 1] : 0,
    }
  }

  const current = sigmaHistory[sigmaHistory.length - 1]
  const mean = sigmaHistory.reduce((a, b) => a + b, 0) / sigmaHistory.length
  const variance = sigmaHistory.reduce((sum, v) => sum + (v - mean) ** 2, 0) / sigmaHistory.length
  const std = Math.sqrt(variance)

  // Regime classification
  let regime: 'low' | 'mid' | 'high'
  if (current < mean - 0.5 * std) {
    regime = 'low'
  } else if (current > mean + 0.5 * std) {
    regime = 'high'
  } else {
    regime = 'mid'
  }

  // Compression: current sigma ≤ 20th percentile
  const sorted = [...sigmaHistory].sort((a, b) => a - b)
  const p20Index = Math.max(0, Math.ceil(sorted.length * 0.2) - 1)
  const p20 = sorted[p20Index]
  const isCompression = current <= p20

  // Transition: sigma > MA + 1.5*std
  const isTransition = current > mean + 1.5 * std

  return { regime, isCompression, isTransition, currentSigma: current }
}

/**
 * Calculate the regime multiplier for range width adjustment.
 *
 * | State            | Multiplier | Rationale                          |
 * |------------------|------------|------------------------------------|
 * | isTransition     | 1.3        | Sudden vol spike → widen for safety |
 * | high             | 1.15       | Sustained high vol → safety margin  |
 * | mid              | 1.0        | No change                           |
 * | low+compression  | 0.75       | Vol stays low → narrow for capital efficiency |
 * | low              | 1.0        | Low but not compressed → keep normal |
 */
export function getRegimeMultiplier(state: RegimeState): number {
  if (state.isTransition) return 1.3
  if (state.regime === 'high') return 1.15
  if (state.regime === 'low' && state.isCompression) return 0.75
  return 1.0
}

/**
 * Calculate cooldown multiplier based on regime.
 *
 * - high vol: 1.5x (extend cooldowns to reduce whipsaw)
 * - low vol: 0.67x (shorten cooldowns, faster re-entry)
 * - mid: 1.0x (unchanged)
 */
export function getCooldownMultiplier(state: RegimeState): number {
  if (state.regime === 'high' || state.isTransition) return 1.5
  if (state.regime === 'low') return 0.67
  return 1.0
}
