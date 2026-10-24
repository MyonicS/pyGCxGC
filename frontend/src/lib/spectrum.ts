/**
 * Mass spectrum display helpers (spec MS-5, MS-7): sticks from the binary
 * spectrum, axis limits, non-overlapping peak labels and hit testing. Only
 * display transforms (ARCH-2); the spectra themselves come from Python.
 */

export interface Sticks {
  mz: Float64Array
  intensity: Float64Array
}

/** Non-zero sticks of column `column` of an `(n, k)` row-major table. */
export function sticksOf(data: Float32Array, nColumns: number, column: number): Sticks {
  const n = Math.floor(data.length / nColumns)
  const mz: number[] = []
  const intensity: number[] = []
  for (let i = 0; i < n; i++) {
    const v = data[i * nColumns + column]
    if (v > 0) {
      mz.push(data[i * nColumns])
      intensity.push(v)
    }
  }
  return { mz: Float64Array.from(mz), intensity: Float64Array.from(intensity) }
}

export function maxOf(values: ArrayLike<number>): number {
  let m = 0
  for (let i = 0; i < values.length; i++) if (values[i] > m) m = values[i]
  return m
}

/** Sticks restricted to an m/z range (the library spectrum to the acquired range). */
export function clipMz(s: Sticks, lo: number, hi: number): Sticks {
  const keep: number[] = []
  for (let i = 0; i < s.mz.length; i++) if (s.mz[i] >= lo && s.mz[i] <= hi) keep.push(i)
  return {
    mz: Float64Array.from(keep, (i) => s.mz[i]),
    intensity: Float64Array.from(keep, (i) => s.intensity[i]),
  }
}

/**
 * m/z axis limits covering every stick of at least `minRel` % of its base
 * peak (as the Tk viewer): small noise far away does not squeeze the plot.
 */
export function mzLimits(spectra: Sticks[], minRel = 1): [number, number] | null {
  let lo = Infinity
  let hi = -Infinity
  for (const s of spectra) {
    const top = maxOf(s.intensity)
    for (let i = 0; i < s.mz.length; i++) {
      if (top > 0 && (s.intensity[i] / top) * 100 >= minRel) {
        lo = Math.min(lo, s.mz[i])
        hi = Math.max(hi, s.mz[i])
      }
    }
  }
  if (!Number.isFinite(lo)) return null
  return [lo - 5, hi + 10]
}

export interface LabelCandidate {
  /** Stick position (px). */
  x: number
  /** Tip of the stick (px); the label sits beyond it. */
  y: number
  text: string
  /** Larger first. */
  priority: number
}

export interface PlacedLabel extends LabelCandidate {
  /** Box (px): the text is centred on `x`. */
  left: number
  right: number
  top: number
  bottom: number
}

/**
 * Labels of the most intense sticks that do not overlap each other (greedy by
 * priority). Labels above a stick (`direction` = -1, SVG y grows downwards)
 * or below it (+1). Boxes stay within `[0, width]`.
 */
export function placeLabels(
  candidates: LabelCandidate[],
  {
    width,
    maxLabels = 10,
    charWidth = 6,
    height = 11,
    gap = 2,
    direction = -1,
  }: {
    width: number
    maxLabels?: number
    charWidth?: number
    height?: number
    gap?: number
    direction?: -1 | 1
  },
): PlacedLabel[] {
  const placed: PlacedLabel[] = []
  const sorted = [...candidates].sort((a, b) => b.priority - a.priority)
  for (const c of sorted) {
    if (placed.length >= maxLabels) break
    const w = c.text.length * charWidth + 2
    const left = c.x - w / 2
    const right = c.x + w / 2
    if (left < 0 || right > width) continue
    const top = direction < 0 ? c.y - gap - height : c.y + gap
    const bottom = top + height
    const overlaps = placed.some(
      (p) => left < p.right && right > p.left && top < p.bottom && bottom > p.top,
    )
    if (!overlaps) placed.push({ ...c, left, right, top, bottom })
  }
  return placed
}

/** Index of the stick nearest to `px` within `radius` px (by x), or -1. */
export function nearestStick(xs: ArrayLike<number>, px: number, radius = 6): number {
  let best = -1
  let bestD = radius
  for (let i = 0; i < xs.length; i++) {
    const d = Math.abs(xs[i] - px)
    if (d <= bestD) {
      bestD = d
      best = i
    }
  }
  return best
}

/** Intensity as shown: relative to the base peak (999) or absolute. */
export function displayIntensity(v: number, top: number, relative: boolean): number {
  return relative ? (top > 0 ? (v / top) * 999 : 0) : v
}
