import { describe, it, expect } from 'vitest'
import { sigmaToTickWidth } from '../../src/strategy/volatility.js'

describe('sigmaToTickWidth (continuous scaling)', () => {
  const sigmaLow = 40
  const sigmaHigh = 120
  const minWidth = 480
  const maxWidth = 1200

  it('returns minWidth when sigma equals sigmaLow', () => {
    expect(sigmaToTickWidth(40, sigmaLow, sigmaHigh, minWidth, maxWidth)).toBe(480)
  })

  it('returns maxWidth when sigma equals sigmaHigh', () => {
    expect(sigmaToTickWidth(120, sigmaLow, sigmaHigh, minWidth, maxWidth)).toBe(1200)
  })

  it('returns midpoint when sigma is midpoint of sigmaLow and sigmaHigh', () => {
    // sigma=80, t=(80-40)/(120-40)=0.5, width=480+720*0.5=840
    expect(sigmaToTickWidth(80, sigmaLow, sigmaHigh, minWidth, maxWidth)).toBe(840)
  })

  it('clamps to minWidth when sigma < sigmaLow', () => {
    expect(sigmaToTickWidth(10, sigmaLow, sigmaHigh, minWidth, maxWidth)).toBe(480)
  })

  it('clamps to maxWidth when sigma > sigmaHigh', () => {
    expect(sigmaToTickWidth(200, sigmaLow, sigmaHigh, minWidth, maxWidth)).toBe(1200)
  })

  it('interpolates linearly for intermediate values', () => {
    // sigma=60, t=(60-40)/(120-40)=0.25, width=480+720*0.25=660
    expect(sigmaToTickWidth(60, sigmaLow, sigmaHigh, minWidth, maxWidth)).toBe(660)
  })

  it('handles sigma=0 (below sigmaLow)', () => {
    expect(sigmaToTickWidth(0, sigmaLow, sigmaHigh, minWidth, maxWidth)).toBe(480)
  })

  it('rounds to nearest integer', () => {
    // sigma=50, t=(50-40)/80=0.125, width=480+720*0.125=570
    expect(sigmaToTickWidth(50, sigmaLow, sigmaHigh, minWidth, maxWidth)).toBe(570)
  })

  it('works with custom parameters', () => {
    // sigmaLow=20, sigmaHigh=100, min=200, max=1000
    // sigma=60, t=(60-20)/80=0.5, width=200+800*0.5=600
    expect(sigmaToTickWidth(60, 20, 100, 200, 1000)).toBe(600)
  })
})
