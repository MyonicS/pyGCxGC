/** Axis and colour bar ticks. */

import { scaleLinear } from 'd3-scale'

import { scaleValue, type ScaleParams } from './scaling'

export interface Tick {
  value: number
  label: string
  /** Position along the axis in [0, 1]. */
  position: number
}

/** "Nice" ticks of a linear axis, about one per `spacing` pixels. */
export function linearTicks(lo: number, hi: number, lengthPx: number, spacing = 80): Tick[] {
  if (!(hi > lo) || !(lengthPx > 0)) return []
  const count = Math.max(2, Math.round(lengthPx / spacing))
  const scale = scaleLinear().domain([lo, hi])
  const format = scale.tickFormat(count)
  return scale.ticks(count).map((value) => ({
    value,
    label: format(value),
    position: (value - lo) / (hi - lo),
  }))
}

/** Compact number for readouts and colour bars ("1.23e5", "0.0421", "12.5", "5000"). */
export function formatValue(v: number, digits = 3): string {
  if (!Number.isFinite(v)) return '–'
  if (v === 0) return '0'
  const a = Math.abs(v)
  if (a >= 1e4 || a < 1e-3) {
    const [m, e] = v.toExponential(digits - 1).split('e')
    return `${trimZeros(m)}e${e.replace('+', '')}`
  }
  // toPrecision switches to exponents from 10^digits on; Number() undoes that
  return String(Number(v.toPrecision(digits)))
}

function trimZeros(s: string): string {
  return s.includes('.') ? s.replace(/\.?0+$/, '') : s
}

/**
 * Colour bar ticks in data units, placed by the current scaling (so a square
 * root or log bar gets ticks spread over its whole length). Coarse ticks are
 * preferred; finer ones fill the gaps the scaling opens near `vmin`. Ticks
 * keep at least `minGapPx` between them.
 */
export function colorbarTicks(p: ScaleParams, lengthPx: number, minGapPx = 26): Tick[] {
  const { vmin, vmax } = p
  if (!(vmax > vmin) || !(lengthPx > 0)) return []
  const accepted: Tick[] = []
  const fits = (pos: number) =>
    accepted.every((t) => Math.abs(t.position - pos) * lengthPx >= minGapPx)
  for (let level = 0; level < 6; level++) {
    const hi = vmin + (vmax - vmin) / 10 ** level
    const scale = scaleLinear().domain([vmin, hi])
    for (const value of scale.ticks(5)) {
      if (value < vmin || value > vmax) continue
      const position = scaleValue(value, p)
      if (!Number.isFinite(position) || !fits(position)) continue
      accepted.push({ value, label: formatValue(value), position })
    }
  }
  return accepted.sort((a, b) => a.position - b.position)
}
