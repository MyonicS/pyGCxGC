import { describe, expect, it } from 'vitest'

import {
  applyScaling,
  invertScaling,
  normalize,
  scaleValue,
  SCALINGS,
  unscaleValue,
} from './scaling'

describe('scaling (spec VIEW-5)', () => {
  it('normalises and clips', () => {
    expect(normalize(5, 0, 10)).toBe(0.5)
    expect(normalize(-3, 0, 10)).toBe(0) // negative (baseline-corrected) values clip at vmin
    expect(normalize(30, 0, 10)).toBe(1)
    expect(Number.isNaN(normalize(NaN, 0, 1))).toBe(true)
    // degenerate range: a step at vmin (as the shader)
    expect(normalize(1, 1, 1)).toBe(0)
    expect(normalize(2, 1, 1)).toBe(1)
  })

  it('implements the formulas of the spec', () => {
    expect(applyScaling(0.25, 'linear')).toBe(0.25)
    expect(applyScaling(0.25, 'sqrt')).toBe(0.5)
    expect(applyScaling(0.125, 'cbrt')).toBeCloseTo(0.5, 12)
    expect(applyScaling(0.5, 'log', 1000)).toBeCloseTo(Math.log(501) / Math.log(1001), 12)
    for (const s of SCALINGS) {
      expect(applyScaling(0, s)).toBe(0)
      expect(applyScaling(1, s)).toBeCloseTo(1, 12)
    }
  })

  it('inverts every scaling', () => {
    for (const s of SCALINGS) {
      for (const t of [0, 0.001, 0.2, 0.5, 0.9, 1]) {
        expect(invertScaling(applyScaling(t, s, 250), s, 250)).toBeCloseTo(t, 10)
      }
    }
  })

  it('maps data values and back', () => {
    const p = { vmin: 10, vmax: 110, scaling: 'sqrt' as const, k: 1000 }
    expect(scaleValue(35, p)).toBe(0.5)
    expect(unscaleValue(0.5, p)).toBeCloseTo(35, 10)
  })
})
