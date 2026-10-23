/**
 * Mask shapes on screen (spec MASK-4, MASK-6): provisional outlines while
 * drawing, handles of a selected shape and hit tests. Shapes are in retention
 * time units (RT1 min, RT2 s); Python rasterises them (MASK-2).
 */

import type { Shape, ShapeInfo } from '@/api/types'
import type { ViewTransform } from '@/lib/geometry'

export type Point = [number, number]

export function isVector(shape: ShapeInfo): shape is Shape {
  return shape.type === 'rect' || shape.type === 'polygon' || shape.type === 'lasso'
}

/** Rectangle through two corners (RT units). */
export function rectShape(a: Point, b: Point): Shape {
  return {
    type: 'rect',
    rt1: [Math.min(a[0], b[0]), Math.max(a[0], b[0])],
    rt2: [Math.min(a[1], b[1]), Math.max(a[1], b[1])],
  }
}

/** Vertices of a shape: rectangle corners counter-clockwise from (rt1 min, rt2 min). */
export function vertices(shape: Shape): Point[] {
  if (shape.type === 'rect') {
    const [x0, x1] = shape.rt1
    const [y0, y1] = shape.rt2
    return [
      [x0, y0],
      [x1, y0],
      [x1, y1],
      [x0, y1],
    ]
  }
  return shape.vertices
}

/** The shape with handle `index` moved to `p` (rectangle corners move two edges). */
export function moveHandle(shape: Shape, index: number, p: Point): Shape {
  if (shape.type === 'rect') {
    const rt1: [number, number] = [...shape.rt1]
    const rt2: [number, number] = [...shape.rt2]
    rt1[index === 0 || index === 3 ? 0 : 1] = p[0]
    rt2[index === 0 || index === 1 ? 0 : 1] = p[1]
    return { type: 'rect', rt1, rt2 }
  }
  const v = shape.vertices.map((q, i): Point => (i === index ? [p[0], p[1]] : q))
  // an edited lasso is an ordinary polygon (it is already simplified)
  return { type: 'polygon', vertices: v }
}

/** The shape moved by `(d1, d2)` (RT units). */
export function translate(shape: Shape, d1: number, d2: number): Shape {
  if (shape.type === 'rect') {
    return {
      type: 'rect',
      rt1: [shape.rt1[0] + d1, shape.rt1[1] + d1],
      rt2: [shape.rt2[0] + d2, shape.rt2[1] + d2],
    }
  }
  return { type: 'polygon', vertices: shape.vertices.map(([a, b]): Point => [a + d1, b + d2]) }
}

/** Point inside a shape (even–odd rule, as the rasteriser). */
export function contains(shape: Shape, [x, y]: Point): boolean {
  if (shape.type === 'rect') {
    return x >= shape.rt1[0] && x <= shape.rt1[1] && y >= shape.rt2[0] && y <= shape.rt2[1]
  }
  const v = shape.vertices
  let inside = false
  for (let i = 0, j = v.length - 1; i < v.length; j = i++) {
    const [xi, yi] = v[i]
    const [xj, yj] = v[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

/** SVG path (screen coordinates inside the plot) of a closed shape or an open polyline. */
export function screenPath(points: Point[], t: ViewTransform, close = true): string {
  if (!points.length) return ''
  const d = points.map(([a, b], i) => `${i ? 'L' : 'M'}${t.x(a).toFixed(1)} ${t.y(b).toFixed(1)}`)
  return d.join('') + (close ? 'Z' : '')
}

/** Index of the handle within `radius` px of a screen point (plot coordinates), or -1. */
export function hitHandle(
  points: Point[],
  t: ViewTransform,
  px: number,
  py: number,
  radius = 7,
): number {
  let best = -1
  let bestD = radius * radius
  points.forEach(([a, b], i) => {
    const d = (t.x(a) - px) ** 2 + (t.y(b) - py) ** 2
    if (d <= bestD) {
      bestD = d
      best = i
    }
  })
  return best
}

/** Screen distance (px) between two RT points. */
export function screenDistance(t: ViewTransform, a: Point, b: Point): number {
  return Math.hypot(t.x(a[0]) - t.x(b[0]), t.y(a[1]) - t.y(b[1]))
}

/** Size of a shape on screen (px), to drop accidental clicks. */
export function screenSize(t: ViewTransform, points: Point[]): [number, number] {
  const xs = points.map((p) => t.x(p[0]))
  const ys = points.map((p) => t.y(p[1]))
  return [Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)]
}

/** Short description of a shape for the Masks panel. */
export function describeShape(shape: ShapeInfo): string {
  if (shape.type === 'rect') {
    const f = (v: number) => v.toFixed(2)
    return `Rectangle ${f(shape.rt1[0])}–${f(shape.rt1[1])} min × ${f(shape.rt2[0])}–${f(shape.rt2[1])} s`
  }
  if (shape.type === 'polygon' || shape.type === 'lasso') {
    return `Polygon, ${shape.vertices.length} vertices`
  }
  return 'Raster (imported TIFF)'
}
