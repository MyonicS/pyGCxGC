import { describe, expect, it } from 'vitest'

import type { DocumentInfo } from '@/api/types'
import type { DocImage, Layer } from '@/store/images'

import { autoVmax, scanAt, valueAt, visibleRegion } from './imageMath'

// full image 4 rows x 6 columns, value = 10 * row + col
const full = {
  rt1: { start: 0, step: 1, count: 6 },
  rt2: { start: 0, step: 0.5, count: 4 },
}
const fullData = Float32Array.from({ length: 24 }, (_, i) => 10 * Math.floor(i / 6) + (i % 6))

function layer(
  data: Float32Array,
  rows: number,
  cols: number,
  pool: [number, number],
  region: Layer['region'],
): Layer {
  return {
    data,
    rows,
    cols,
    pool,
    region,
    geometry: {
      rt1: { start: region[2], step: pool[1], count: cols },
      rt2: { start: region[0] * 0.5, step: 0.5 * pool[0], count: rows },
    },
  }
}

function image(overview: Layer, detail: Layer | null = null): DocImage {
  return {
    docId: 'd',
    revision: 1,
    signal: 'tic',
    mz: [],
    full,
    shift: 0,
    overview,
    detail,
    range: [0, 35],
  }
}

describe('image lookups', () => {
  it('reads full-resolution values', () => {
    const img = image(layer(fullData, 4, 6, [1, 1], [0, 4, 0, 6]))
    expect(valueAt(img, 2, 3)).toEqual({ value: 23, pooled: false })
    // phase-shift preview: displayed row r shows row (r + s) mod n
    expect(valueAt(img, 3, 1, 2)?.value).toBe(11)
  })

  it('prefers the detail layer over a pooled overview', () => {
    // 2 x 2 max pool of the full image
    const pooled = new Float32Array([11, 13, 15, 31, 33, 35])
    const detail = layer(Float32Array.from([21, 22, 31, 32]), 2, 2, [1, 1], [2, 4, 1, 3])
    const img = image(layer(pooled, 2, 3, [2, 2], [0, 4, 0, 6]), detail)
    expect(valueAt(img, 0, 5)).toEqual({ value: 15, pooled: true })
    expect(valueAt(img, 3, 2)).toEqual({ value: 32, pooled: false })
    expect(valueAt(img, 3, 4)).toEqual({ value: 35, pooled: true })
  })

  it('finds the visible region', () => {
    expect(visibleRegion(full, { x0: 1.5, x1: 3.2, y0: 0.6, y1: 2 })).toEqual([1, 4, 1, 4])
  })

  it('takes the automatic vmax from the visible data', () => {
    const img = image(layer(fullData, 4, 6, [1, 1], [0, 4, 0, 6]))
    expect(autoVmax(img)).toBeCloseTo(34.977, 3) // 99.9th percentile of 0..35
    expect(autoVmax(img, { x0: 0, x1: 2, y0: 0, y1: 1 })).toBeCloseTo(10.997, 3)
    const zeros = image(layer(new Float32Array(24), 4, 6, [1, 1], [0, 4, 0, 6]))
    expect(autoVmax(zeros)).toBe(1)
  })

  it('numbers scans like GCxGC_MS.scan_at', () => {
    const doc = {
      kind: 'ms',
      n_scans: 100,
      params: { shift: 3 },
      grid: { n_rt2: 20 },
    } as unknown as DocumentInfo
    expect(scanAt(doc, 5, 2)).toBe(48)
    expect(scanAt(doc, 19, 4)).toBe(null) // 4 * 20 + 19 + 3 = 102 >= 100
    expect(scanAt({ ...doc, kind: 'fid' } as DocumentInfo, 5, 2)).toBe(null)
  })
})
