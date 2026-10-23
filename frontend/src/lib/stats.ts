/**
 * Statistics of displayed images: value range, percentiles and histograms.
 * These only drive the display (contrast, histogram widget), never results.
 */

export interface Region {
  /** Half-open row range. */
  r0: number
  r1: number
  /** Half-open column range. */
  c0: number
  c1: number
}

/** Finite values of a row-major `rows × cols` image inside `region`. */
export function finiteValues(data: ArrayLike<number>, cols: number, region: Region): Float64Array {
  const out = new Float64Array((region.r1 - region.r0) * (region.c1 - region.c0))
  let n = 0
  for (let r = region.r0; r < region.r1; r++) {
    const base = r * cols
    for (let c = region.c0; c < region.c1; c++) {
      const v = data[base + c]
      if (Number.isFinite(v)) out[n++] = v
    }
  }
  return out.subarray(0, n)
}

/** `[min, max]` of the finite values (`[0, 0]` when there are none). */
export function finiteRange(data: ArrayLike<number>): [number, number] {
  let lo = Infinity
  let hi = -Infinity
  for (let i = 0; i < data.length; i++) {
    const v = data[i]
    if (v < lo) lo = v
    if (v > hi) hi = v
  }
  return lo <= hi ? [lo, hi] : [0, 0]
}

/** k-th smallest value (0-based), reordering `a` in place (quickselect). */
function select(a: Float64Array, k: number, lo = 0, hi = a.length - 1): number {
  while (hi > lo) {
    // median of three as pivot
    const mid = (lo + hi) >> 1
    if (a[mid] < a[lo]) swap(a, mid, lo)
    if (a[hi] < a[lo]) swap(a, hi, lo)
    if (a[hi] < a[mid]) swap(a, hi, mid)
    const pivot = a[mid]
    let i = lo
    let j = hi
    while (i <= j) {
      while (a[i] < pivot) i++
      while (a[j] > pivot) j--
      if (i <= j) {
        swap(a, i, j)
        i++
        j--
      }
    }
    if (k <= j) hi = j
    else if (k >= i) lo = i
    else return a[k]
  }
  return a[k]
}

function swap(a: Float64Array, i: number, j: number) {
  const t = a[i]
  a[i] = a[j]
  a[j] = t
}

/**
 * Percentile `q` (0–100) with linear interpolation, like `numpy.percentile`.
 * Reorders `values` in place. Returns NaN for an empty array.
 */
export function percentileInPlace(values: Float64Array, q: number): number {
  const n = values.length
  if (n === 0) return NaN
  const pos = (Math.min(Math.max(q, 0), 100) / 100) * (n - 1)
  const lo = Math.floor(pos)
  const vlo = select(values, lo)
  if (pos === lo) return vlo
  // after selecting `lo`, the next order statistic is the minimum above it
  let vhi = Infinity
  for (let i = lo + 1; i < n; i++) if (values[i] < vhi) vhi = values[i]
  return vlo + (vhi - vlo) * (pos - lo)
}

export function percentile(values: ArrayLike<number>, q: number): number {
  const copy = Float64Array.from(values as ArrayLike<number>)
  return percentileInPlace(copy, q)
}

export interface Histogram {
  /** Bin edges in data units (`bins + 1` of them). */
  edges: Float64Array
  counts: Uint32Array
  max: number
}

/**
 * Histogram of the finite values. With `transform`, the bins are uniform in
 * `transform(value)` (e.g. the display scaling), mapped back with `inverse`.
 */
export function histogram(
  data: ArrayLike<number>,
  bins: number,
  lo: number,
  hi: number,
  transform: (v: number) => number = (v) => v,
  inverse: (u: number) => number = (u) => u,
): Histogram {
  const counts = new Uint32Array(bins)
  const edges = new Float64Array(bins + 1)
  const ulo = transform(lo)
  const uhi = transform(hi)
  for (let i = 0; i <= bins; i++) edges[i] = inverse(ulo + ((uhi - ulo) * i) / bins)
  const width = uhi - ulo
  if (width > 0) {
    for (let i = 0; i < data.length; i++) {
      const v = data[i]
      if (!Number.isFinite(v) || v < lo || v > hi) continue
      const b = Math.min(bins - 1, Math.floor(((transform(v) - ulo) / width) * bins))
      counts[b]++
    }
  }
  let max = 0
  for (let i = 0; i < bins; i++) if (counts[i] > max) max = counts[i]
  return { edges, counts, max }
}
