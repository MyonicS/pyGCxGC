import { describe, expect, it } from 'vitest'

import { finiteRange, finiteValues, histogram, percentile } from './stats'

/** numpy.percentile with the default linear method, by sorting. */
function reference(values: number[], q: number) {
  const a = [...values].sort((x, y) => x - y)
  const pos = (q / 100) * (a.length - 1)
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  return a[lo] + (a[hi] - a[lo]) * (pos - lo)
}

function random(n: number, seed: number) {
  let s = seed
  return Array.from({ length: n }, () => {
    s = (s * 1103515245 + 12345) % 2 ** 31
    return (s / 2 ** 31) * 100 - 20
  })
}

describe('percentile', () => {
  it('interpolates like numpy', () => {
    expect(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 25)).toBe(3.25)
    expect(percentile([5], 99.9)).toBe(5)
    expect(Number.isNaN(percentile([], 50))).toBe(true)
  })

  it('agrees with sorting on random data with ties', () => {
    for (const [n, seed] of [
      [2, 1],
      [17, 2],
      [1000, 3],
      [4097, 4],
    ]) {
      const values = random(n, seed).map((v, i) => (i % 7 === 0 ? 3 : v))
      for (const q of [0, 1, 12.5, 50, 90, 99.9, 100]) {
        expect(percentile(values, q)).toBeCloseTo(reference(values, q), 9)
      }
    }
  })
})

describe('image statistics', () => {
  it('ignores non-finite values', () => {
    const data = new Float32Array([1, NaN, -Infinity, 4, 2, Infinity])
    expect(finiteRange(new Float32Array([NaN]))).toEqual([0, 0])
    // 2 x 3 image, region rows [0, 2) cols [0, 2)
    expect(Array.from(finiteValues(data, 3, { r0: 0, r1: 2, c0: 0, c1: 2 }))).toEqual([1, 4, 2])
  })

  it('bins uniformly in a transform', () => {
    const data = [0, 1, 4, 9, 16]
    const h = histogram(data, 4, 0, 16, Math.sqrt, (u) => u * u)
    expect(Array.from(h.edges)).toEqual([0, 1, 4, 9, 16])
    expect(Array.from(h.counts)).toEqual([1, 1, 1, 2])
    expect(h.max).toBe(2)
  })
})
