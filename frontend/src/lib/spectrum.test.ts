import { describe, expect, it } from 'vitest'

import {
  clipMz,
  displayIntensity,
  mzLimits,
  nearestStick,
  placeLabels,
  sticksOf,
  type Sticks,
} from './spectrum'

const sticks = (pairs: [number, number][]): Sticks => ({
  mz: Float64Array.from(pairs, (p) => p[0]),
  intensity: Float64Array.from(pairs, (p) => p[1]),
})

describe('spectrum helpers', () => {
  it('reads non-zero sticks of a column', () => {
    // rows of [mz, raw, corrected]
    const table = Float32Array.from([45, 5, 0, 46, 9, 3, 47, 2, 0, 91, 100, 80])
    const s = sticksOf(table, 3, 2)
    expect(Array.from(s.mz)).toEqual([46, 91])
    expect(Array.from(s.intensity)).toEqual([3, 80])
    expect(Array.from(sticksOf(table, 3, 1).mz)).toEqual([45, 46, 47, 91])
  })

  it('clips to an m/z range', () => {
    const s = clipMz(
      sticks([
        [12, 5],
        [50, 10],
        [600, 3],
      ]),
      45,
      500,
    )
    expect(Array.from(s.mz)).toEqual([50])
  })

  it('limits the m/z axis to significant peaks', () => {
    const a = sticks([
      [45, 0.5], // below 1 %
      [91, 999],
      [92, 700],
    ])
    const b = sticks([[128, 50]])
    expect(mzLimits([a])).toEqual([86, 102])
    expect(mzLimits([a, b])).toEqual([86, 138])
    expect(mzLimits([sticks([])])).toBeNull()
  })

  it('places labels by priority without overlaps', () => {
    const labels = placeLabels(
      [
        { x: 100, y: 50, text: '91', priority: 999 },
        { x: 104, y: 52, text: '92', priority: 700 }, // overlaps 91
        { x: 104, y: 120, text: '93', priority: 10 }, // lower: fits below
        { x: 200, y: 80, text: '65', priority: 120 },
        { x: 2, y: 80, text: '45', priority: 500 }, // outside the plot
      ],
      { width: 300 },
    )
    expect(labels.map((l) => l.text)).toEqual(['91', '65', '93'])
    expect(labels[0].bottom).toBe(48)
    const below = placeLabels([{ x: 100, y: 50, text: '91', priority: 1 }], {
      width: 300,
      direction: 1,
    })
    expect(below[0].top).toBe(52)
    expect(
      placeLabels([{ x: 100, y: 50, text: '91', priority: 1 }], { width: 300, maxLabels: 0 }),
    ).toHaveLength(0)
  })

  it('finds the nearest stick', () => {
    expect(nearestStick([10, 20, 30], 22)).toBe(1)
    expect(nearestStick([10, 20, 30], 45)).toBe(-1)
  })

  it('scales to 999', () => {
    expect(displayIntensity(50, 200, true)).toBeCloseTo(249.75)
    expect(displayIntensity(50, 200, false)).toBe(50)
    expect(displayIntensity(50, 0, true)).toBe(0)
  })
})
