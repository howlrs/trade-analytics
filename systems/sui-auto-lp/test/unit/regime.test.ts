import { describe, it, expect } from 'vitest'
import { detectRegime, getRegimeMultiplier, getCooldownMultiplier } from '../../src/strategy/regime.js'

describe('detectRegime', () => {
  it('returns mid with no flags when history has fewer than 3 entries', () => {
    const result = detectRegime([50, 60])
    expect(result.regime).toBe('mid')
    expect(result.isCompression).toBe(false)
    expect(result.isTransition).toBe(false)
    expect(result.currentSigma).toBe(60)
  })

  it('returns mid with currentSigma=0 for empty history', () => {
    const result = detectRegime([])
    expect(result.regime).toBe('mid')
    expect(result.currentSigma).toBe(0)
  })

  it('detects low regime when sigma < MA - 0.5*std', () => {
    // History: [100, 100, 100, 20] — MA=80, std≈34.6, MA-0.5*std≈62.7
    // current=20 < 62.7 → low
    const result = detectRegime([100, 100, 100, 20])
    expect(result.regime).toBe('low')
  })

  it('detects high regime when sigma > MA + 0.5*std', () => {
    // History: [20, 20, 20, 100] — MA=40, std≈34.6, MA+0.5*std≈57.3
    // current=100 > 57.3 → high
    const result = detectRegime([20, 20, 20, 100])
    expect(result.regime).toBe('high')
  })

  it('detects mid regime when sigma is near MA', () => {
    // Values with some spread; current (50) is within MA ± 0.5*std
    // [30, 50, 70, 50] → MA=50, std≈14.1, range [42.9, 57.1], current=50 → mid
    const result = detectRegime([30, 50, 70, 50])
    expect(result.regime).toBe('mid')
  })

  it('detects compression when sigma <= p20 of history', () => {
    // History sorted: [10, 20, 50, 60, 70, 80, 90, 100, 110, 120]
    // p20 index = ceil(10 * 0.2) - 1 = 1, p20 = 20
    // current=10 <= 20 → isCompression
    const history = [50, 60, 70, 80, 90, 100, 110, 120, 20, 10]
    const result = detectRegime(history)
    expect(result.isCompression).toBe(true)
  })

  it('does not detect compression when sigma > p20', () => {
    const history = [50, 60, 70, 80, 90]
    const result = detectRegime(history)
    expect(result.isCompression).toBe(false)
  })

  it('detects transition when sigma > MA + 1.5*std', () => {
    // History: [40, 40, 40, 40, 200]
    // MA=72, variance=((32^2*4+128^2)/5)=3481.6, std≈59, MA+1.5*std≈160.5
    // current=200 > 160.5 → isTransition
    const result = detectRegime([40, 40, 40, 40, 200])
    expect(result.isTransition).toBe(true)
  })

  it('does not detect transition when sigma is within normal range', () => {
    const result = detectRegime([50, 52, 48, 51, 53])
    expect(result.isTransition).toBe(false)
  })
})

describe('getRegimeMultiplier', () => {
  it('returns 1.3 for transition state', () => {
    expect(getRegimeMultiplier({
      regime: 'high', isCompression: false, isTransition: true, currentSigma: 200,
    })).toBe(1.3)
  })

  it('returns 1.15 for high regime (no transition)', () => {
    expect(getRegimeMultiplier({
      regime: 'high', isCompression: false, isTransition: false, currentSigma: 150,
    })).toBe(1.15)
  })

  it('returns 0.75 for low regime with compression', () => {
    expect(getRegimeMultiplier({
      regime: 'low', isCompression: true, isTransition: false, currentSigma: 10,
    })).toBe(0.75)
  })

  it('returns 1.0 for low regime without compression', () => {
    expect(getRegimeMultiplier({
      regime: 'low', isCompression: false, isTransition: false, currentSigma: 30,
    })).toBe(1.0)
  })

  it('returns 1.0 for mid regime', () => {
    expect(getRegimeMultiplier({
      regime: 'mid', isCompression: false, isTransition: false, currentSigma: 60,
    })).toBe(1.0)
  })

  it('transition takes priority over high regime', () => {
    // isTransition = true should return 1.3, not 1.15
    expect(getRegimeMultiplier({
      regime: 'high', isCompression: false, isTransition: true, currentSigma: 200,
    })).toBe(1.3)
  })
})

describe('getCooldownMultiplier', () => {
  it('returns 1.5 for high regime', () => {
    expect(getCooldownMultiplier({
      regime: 'high', isCompression: false, isTransition: false, currentSigma: 150,
    })).toBe(1.5)
  })

  it('returns 1.5 for transition', () => {
    expect(getCooldownMultiplier({
      regime: 'mid', isCompression: false, isTransition: true, currentSigma: 200,
    })).toBe(1.5)
  })

  it('returns 0.67 for low regime', () => {
    expect(getCooldownMultiplier({
      regime: 'low', isCompression: false, isTransition: false, currentSigma: 20,
    })).toBe(0.67)
  })

  it('returns 1.0 for mid regime', () => {
    expect(getCooldownMultiplier({
      regime: 'mid', isCompression: false, isTransition: false, currentSigma: 60,
    })).toBe(1.0)
  })
})
