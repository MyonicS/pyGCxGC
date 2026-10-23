import { describe, expect, it } from 'vitest'

import { ViewTransform } from '@/lib/geometry'

import {
  contains,
  hitHandle,
  moveHandle,
  rectShape,
  screenPath,
  translate,
  vertices,
  type Point,
} from './shapes'

const rect = rectShape([4, 2], [1, 6])
const tri = {
  type: 'polygon' as const,
  vertices: [
    [0, 0],
    [4, 0],
    [0, 4],
  ] as Point[],
}

describe('mask shapes', () => {
  it('normalises rectangles', () => {
    expect(rect).toEqual({ type: 'rect', rt1: [1, 4], rt2: [2, 6] })
    expect(vertices(rect)).toEqual([
      [1, 2],
      [4, 2],
      [4, 6],
      [1, 6],
    ])
  })

  it('moves handles', () => {
    // corner 2 is (rt1 max, rt2 max)
    expect(moveHandle(rect, 2, [5, 7])).toEqual({ type: 'rect', rt1: [1, 5], rt2: [2, 7] })
    expect(moveHandle(rect, 0, [0, 1])).toEqual({ type: 'rect', rt1: [0, 4], rt2: [1, 6] })
    const lasso = { type: 'lasso' as const, vertices: tri.vertices }
    expect(moveHandle(lasso, 1, [5, 1])).toEqual({
      type: 'polygon',
      vertices: [
        [0, 0],
        [5, 1],
        [0, 4],
      ],
    })
  })

  it('translates', () => {
    expect(translate(rect, 1, -1)).toEqual({ type: 'rect', rt1: [2, 5], rt2: [1, 5] })
    expect(translate(tri, 1, 1).type).toBe('polygon')
  })

  it('tests points with the even-odd rule', () => {
    expect(contains(rect, [2, 3])).toBe(true)
    expect(contains(rect, [0, 3])).toBe(false)
    expect(contains(tri, [1, 1])).toBe(true)
    expect(contains(tri, [3, 3])).toBe(false)
    // a self-intersecting bow tie: the crossing region counts once per lobe
    const bow = {
      type: 'polygon' as const,
      vertices: [
        [0, 0],
        [4, 4],
        [4, 0],
        [0, 4],
      ] as Point[],
    }
    expect(contains(bow, [1, 2])).toBe(true)
    expect(contains(bow, [2, 1])).toBe(false)
  })

  it('maps to the screen and finds handles', () => {
    const t = new ViewTransform({ x0: 0, x1: 10, y0: 0, y1: 10 }, 100, 100)
    expect(screenPath(tri.vertices, t)).toBe('M0.0 100.0L40.0 100.0L0.0 60.0Z')
    expect(screenPath([[1, 1]], t, false)).toBe('M10.0 90.0')
    expect(hitHandle(tri.vertices, t, 41, 98)).toBe(1)
    expect(hitHandle(tri.vertices, t, 20, 80)).toBe(-1)
  })
})
