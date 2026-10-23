/**
 * Image geometry and view transforms (spec CONV-3).
 *
 * Pixel `(row, col)` is the rectangle RT1 in `[rt1.start + col·step, +step)`
 * (min) and RT2 in `[rt2.start + row·step, +step)` (s). Row 0 is the lowest
 * RT2, so the y axis increases upwards.
 */

export interface Axis {
  start: number
  step: number
  count: number
}

export interface ImageGeometry {
  rt1: Axis
  rt2: Axis
}

/** Visible data range: RT1 (min) on x, RT2 (s) on y. */
export interface Domain {
  x0: number
  x1: number
  y0: number
  y1: number
}

export interface Pixel {
  row: number
  col: number
}

export const axisEnd = (a: Axis) => a.start + a.count * a.step

export function fullDomain(g: ImageGeometry): Domain {
  return { x0: g.rt1.start, x1: axisEnd(g.rt1), y0: g.rt2.start, y1: axisEnd(g.rt2) }
}

/** Index of the pixel containing `v` (may lie outside `[0, count)`). */
export const indexAt = (a: Axis, v: number) => Math.floor((v - a.start) / a.step)

/** Pixel containing a retention time pair, or `null` outside the image. */
export function pixelAt(g: ImageGeometry, rt1: number, rt2: number): Pixel | null {
  const col = indexAt(g.rt1, rt1)
  const row = indexAt(g.rt2, rt2)
  if (col < 0 || col >= g.rt1.count || row < 0 || row >= g.rt2.count) return null
  return { row, col }
}

export function pixelCentre(g: ImageGeometry, p: Pixel): { rt1: number; rt2: number } {
  return {
    rt1: g.rt1.start + (p.col + 0.5) * g.rt1.step,
    rt2: g.rt2.start + (p.row + 0.5) * g.rt2.step,
  }
}

/** Half-open index range `[i0, i1)` of the pixels overlapping `[lo, hi]`. */
export function indexRange(a: Axis, lo: number, hi: number): [number, number] {
  const i0 = Math.max(0, Math.min(a.count, indexAt(a, lo)))
  const i1 = Math.max(0, Math.min(a.count, Math.ceil((hi - a.start) / a.step)))
  return [i0, Math.max(i0, i1)]
}

export const spanX = (d: Domain) => d.x1 - d.x0
export const spanY = (d: Domain) => d.y1 - d.y0

/**
 * Zoom about an anchor point: spans are multiplied by `fx` and `fy`
 * (`< 1` zooms in) and the anchor stays at the same screen position.
 */
export function zoomDomain(d: Domain, ax: number, ay: number, fx: number, fy: number): Domain {
  return {
    x0: ax - (ax - d.x0) * fx,
    x1: ax + (d.x1 - ax) * fx,
    y0: ay - (ay - d.y0) * fy,
    y1: ay + (d.y1 - ay) * fy,
  }
}

export function panDomain(d: Domain, dx: number, dy: number): Domain {
  return { x0: d.x0 + dx, x1: d.x1 + dx, y0: d.y0 + dy, y1: d.y1 + dy }
}

function constrainAxis(
  lo: number,
  hi: number,
  min: number,
  max: number,
  minSpan: number,
): [number, number] {
  const full = max - min
  let span = Math.min(Math.max(hi - lo, Math.min(minSpan, full)), full)
  if (!(span > 0)) span = full
  const centre = (lo + hi) / 2
  let a = centre - span / 2
  a = Math.min(Math.max(a, min), max - span)
  return [a, a + span]
}

/**
 * Keep a domain inside the image: spans between `minSpan` and the full
 * extent, shifted back inside when panned past an edge.
 */
export function constrainDomain(
  d: Domain,
  full: Domain,
  minSpanX: number,
  minSpanY: number,
): Domain {
  const [x0, x1] = constrainAxis(d.x0, d.x1, full.x0, full.x1, minSpanX)
  const [y0, y1] = constrainAxis(d.y0, d.y1, full.y0, full.y1, minSpanY)
  return { x0, x1, y0, y1 }
}

export function sameDomain(a: Domain, b: Domain, eps = 1e-12): boolean {
  return (
    Math.abs(a.x0 - b.x0) <= eps &&
    Math.abs(a.x1 - b.x1) <= eps &&
    Math.abs(a.y0 - b.y0) <= eps &&
    Math.abs(a.y1 - b.y1) <= eps
  )
}

/** Linear map between a domain and a plot of `width × height` CSS pixels. */
export class ViewTransform {
  readonly domain: Domain
  readonly width: number
  readonly height: number

  constructor(domain: Domain, width: number, height: number) {
    this.domain = domain
    this.width = width
    this.height = height
  }

  x(rt1: number): number {
    return ((rt1 - this.domain.x0) / spanX(this.domain)) * this.width
  }

  y(rt2: number): number {
    return this.height - ((rt2 - this.domain.y0) / spanY(this.domain)) * this.height
  }

  rt1(px: number): number {
    return this.domain.x0 + (px / this.width) * spanX(this.domain)
  }

  rt2(py: number): number {
    return this.domain.y0 + ((this.height - py) / this.height) * spanY(this.domain)
  }
}
