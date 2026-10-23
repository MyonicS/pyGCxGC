import { describe, expect, it } from 'vitest'

import { colorbarTicks, formatValue, linearTicks } from './ticks'

describe('ticks', () => {
  it('makes nice linear ticks', () => {
    const ticks = linearTicks(0, 100, 800)
    expect(ticks.map((t) => t.value)).toEqual([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    expect(ticks[5].position).toBe(0.5)
    expect(linearTicks(1, 1, 100)).toEqual([])
  })

  it('spreads colour bar ticks over a square-root bar', () => {
    const p = { vmin: 0, vmax: 1000, scaling: 'sqrt' as const, k: 1000 }
    const ticks = colorbarTicks(p, 400, 18)
    const positions = ticks.map((t) => t.position)
    expect(positions).toEqual([...positions].sort((a, b) => a - b))
    for (let i = 1; i < positions.length; i++) {
      expect((positions[i] - positions[i - 1]) * 400).toBeGreaterThanOrEqual(18)
    }
    // the lower part of a square-root bar gets ticks too
    expect(ticks.some((t) => t.value > 0 && t.value <= 50)).toBe(true)
    expect(ticks.find((t) => t.value === 400)?.position).toBeCloseTo(Math.sqrt(0.4), 12)
  })

  it('formats compactly', () => {
    expect(formatValue(0)).toBe('0')
    expect(formatValue(12.5)).toBe('12.5')
    expect(formatValue(0.0421)).toBe('0.0421')
    expect(formatValue(5000)).toBe('5000')
    expect(formatValue(1234.5)).toBe('1230')
    expect(formatValue(1234.5, 4)).toBe('1235')
    expect(formatValue(50000)).toBe('5e4')
    expect(formatValue(123456)).toBe('1.23e5')
    expect(formatValue(-0.000012)).toBe('-1.2e-5')
    expect(formatValue(NaN)).toBe('–')
  })
})
