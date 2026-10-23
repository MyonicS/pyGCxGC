import { describe, expect, it } from 'vitest'

import {
  constrainDomain,
  fullDomain,
  indexRange,
  pixelAt,
  pixelCentre,
  ViewTransform,
  zoomDomain,
  type ImageGeometry,
} from './geometry'

// 20 s modulation (1/3 min per column), 0.05 s sampling
const g: ImageGeometry = {
  rt1: { start: 2, step: 1 / 3, count: 30 },
  rt2: { start: 0, step: 0.05, count: 400 },
}

describe('pixel geometry (spec CONV-3)', () => {
  it('maps a pixel rectangle [start, start + step)', () => {
    expect(pixelAt(g, 2, 0)).toEqual({ row: 0, col: 0 })
    expect(pixelAt(g, 2 + 1 / 3 - 1e-9, 0.05 - 1e-9)).toEqual({ row: 0, col: 0 })
    expect(pixelAt(g, 2 + 1 / 3, 0.05)).toEqual({ row: 1, col: 1 })
    expect(pixelAt(g, 1.99, 1)).toBeNull()
    expect(pixelAt(g, 3, 20)).toBeNull() // rt2 = 20 s is past the last row
  })

  it('round-trips pixel centres', () => {
    const c = pixelCentre(g, { row: 17, col: 4 })
    expect(c.rt1).toBeCloseTo(2 + 4.5 / 3, 12)
    expect(c.rt2).toBeCloseTo(17.5 * 0.05, 12)
    expect(pixelAt(g, c.rt1, c.rt2)).toEqual({ row: 17, col: 4 })
  })

  it('finds the pixels overlapping a range', () => {
    expect(indexRange(g.rt2, 0, 20)).toEqual([0, 400])
    expect(indexRange(g.rt2, 0.07, 0.11)).toEqual([1, 3])
    expect(indexRange(g.rt2, -5, -1)).toEqual([0, 0])
  })
})

describe('view transforms', () => {
  it('zooms about an anchor', () => {
    const d = { x0: 0, x1: 10, y0: 0, y1: 4 }
    const z = zoomDomain(d, 2, 1, 0.5, 0.25)
    expect(z).toEqual({ x0: 1, x1: 6, y0: 0.75, y1: 1.75 })
    // the anchor keeps its screen position
    const before = new ViewTransform(d, 100, 40)
    const after = new ViewTransform(z, 100, 40)
    expect(after.x(2)).toBeCloseTo(before.x(2), 12)
    expect(after.y(1)).toBeCloseTo(before.y(1), 12)
  })

  it('puts RT2 upwards', () => {
    const t = new ViewTransform({ x0: 0, x1: 10, y0: 0, y1: 4 }, 200, 100)
    expect(t.y(0)).toBe(100)
    expect(t.y(4)).toBe(0)
    expect(t.rt2(t.y(1.3))).toBeCloseTo(1.3, 12)
    expect(t.rt1(t.x(7.7))).toBeCloseTo(7.7, 12)
  })

  it('keeps the domain inside the image', () => {
    const full = fullDomain(g)
    expect(full).toEqual({ x0: 2, x1: 12, y0: 0, y1: 20 })
    // zoomed out too far: the whole image
    expect(constrainDomain({ x0: -10, x1: 30, y0: -5, y1: 40 }, full, 0.1, 0.1)).toEqual(full)
    // panned past the left edge: shifted back, span kept
    const d = constrainDomain({ x0: 0, x1: 4, y0: 5, y1: 6 }, full, 0.1, 0.1)
    expect(d).toEqual({ x0: 2, x1: 6, y0: 5, y1: 6 })
    // a minimum span
    const tiny = constrainDomain({ x0: 5, x1: 5.0001, y0: 5, y1: 5 }, full, 0.5, 0.1)
    expect(tiny.x1 - tiny.x0).toBeCloseTo(0.5, 12)
    expect(tiny.y1 - tiny.y0).toBeCloseTo(0.1, 12)
  })
})
