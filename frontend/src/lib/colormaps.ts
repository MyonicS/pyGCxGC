/**
 * Colour maps (spec VIEW-4), generated from matplotlib by
 * `scripts/export_colormaps.py`: 256 entries each, the same 8-bit colours
 * matplotlib renders.
 */

import data from '@/colormaps.json'

export const COLORMAPS = [
  'viridis',
  'plasma',
  'inferno',
  'magma',
  'cividis',
  'turbo',
  'jet',
  'gray',
] as const
export type ColormapName = (typeof COLORMAPS)[number]

export const LUT_SIZE = 256

const cache = new Map<string, Uint8Array>()

/** RGBA lookup table (256 × 4 bytes) of a colour map, optionally reversed. */
export function colormapLut(name: ColormapName, reverse = false): Uint8Array {
  const key = `${name}:${reverse}`
  let lut = cache.get(key)
  if (!lut) {
    const hex = (data.maps as Record<string, string>)[name]
    if (!hex) throw new Error(`Unknown colour map ${name}`)
    lut = new Uint8Array(LUT_SIZE * 4)
    for (let i = 0; i < LUT_SIZE; i++) {
      const j = reverse ? LUT_SIZE - 1 - i : i
      for (let c = 0; c < 3; c++) {
        lut[i * 4 + c] = parseInt(hex.slice(j * 6 + c * 2, j * 6 + c * 2 + 2), 16)
      }
      lut[i * 4 + 3] = 255
    }
    cache.set(key, lut)
  }
  return lut
}

/**
 * LUT index of a scaled value `s` in [0, 1], as matplotlib's
 * `Colormap.__call__`: `min(floor(s * N), N - 1)`.
 */
export function lutIndex(s: number): number {
  return Math.min(Math.max(Math.floor(s * LUT_SIZE), 0), LUT_SIZE - 1)
}

/** `[r, g, b]` of a scaled value. */
export function colorAt(name: ColormapName, s: number, reverse = false): [number, number, number] {
  const lut = colormapLut(name, reverse)
  const i = lutIndex(s) * 4
  return [lut[i], lut[i + 1], lut[i + 2]]
}

/** CSS gradient of a colour map, for previews. */
export function colormapGradient(name: ColormapName, reverse = false, stops = 16): string {
  const parts: string[] = []
  for (let i = 0; i < stops; i++) {
    const s = i / (stops - 1)
    const [r, g, b] = colorAt(name, Math.min(s, 0.999999), reverse)
    parts.push(`rgb(${r} ${g} ${b}) ${(s * 100).toFixed(1)}%`)
  }
  return `linear-gradient(to right, ${parts.join(', ')})`
}

export function isColormap(name: unknown): name is ColormapName {
  return typeof name === 'string' && (COLORMAPS as readonly string[]).includes(name)
}
