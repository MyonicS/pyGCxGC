/**
 * Display scaling (spec VIEW-5). The fragment shader implements the same
 * formulas; these CPU versions drive the colour bar, the histogram and the
 * tests.
 *
 * With `t = clip((x - vmin) / (vmax - vmin), 0, 1)`:
 * linear `t`, square root `√t`, cube root `∛t`, log `log(1 + k t) / log(1 + k)`.
 */

export const SCALINGS = ['linear', 'sqrt', 'cbrt', 'log'] as const
export type Scaling = (typeof SCALINGS)[number]

export const SCALING_LABELS: Record<Scaling, string> = {
  linear: 'Linear',
  sqrt: 'Square root',
  cbrt: 'Cube root',
  log: 'Log',
}

/** Index of a scaling in the shader's `u_scaling` uniform. */
export const SCALING_INDEX: Record<Scaling, number> = { linear: 0, sqrt: 1, cbrt: 2, log: 3 }

export const DEFAULT_LOG_RANGE = 1000

export interface ScaleParams {
  vmin: number
  vmax: number
  scaling: Scaling
  /** Dynamic range `k` of the log scaling. */
  k: number
}

/** `clip((x - vmin) / (vmax - vmin), 0, 1)`; NaN stays NaN. */
export function normalize(x: number, vmin: number, vmax: number): number {
  if (Number.isNaN(x)) return NaN
  const range = vmax - vmin
  if (!(range > 0)) return x > vmin ? 1 : 0
  return Math.min(Math.max((x - vmin) / range, 0), 1)
}

/** Scaled value in [0, 1] of a normalised `t`. */
export function applyScaling(t: number, scaling: Scaling, k = DEFAULT_LOG_RANGE): number {
  switch (scaling) {
    case 'linear':
      return t
    case 'sqrt':
      return Math.sqrt(t)
    case 'cbrt':
      return Math.cbrt(t)
    case 'log':
      return k > 0 ? Math.log1p(k * t) / Math.log1p(k) : t
  }
}

/** Inverse of {@link applyScaling}. */
export function invertScaling(s: number, scaling: Scaling, k = DEFAULT_LOG_RANGE): number {
  switch (scaling) {
    case 'linear':
      return s
    case 'sqrt':
      return s * s
    case 'cbrt':
      return s * s * s
    case 'log':
      return k > 0 ? Math.expm1(s * Math.log1p(k)) / k : s
  }
}

/** Data value -> position in [0, 1] of the colour map. */
export function scaleValue(x: number, p: ScaleParams): number {
  return applyScaling(normalize(x, p.vmin, p.vmax), p.scaling, p.k)
}

/** Position in [0, 1] of the colour map -> data value. */
export function unscaleValue(s: number, p: ScaleParams): number {
  return p.vmin + invertScaling(s, p.scaling, p.k) * (p.vmax - p.vmin)
}
