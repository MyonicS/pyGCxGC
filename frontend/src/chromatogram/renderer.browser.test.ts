/**
 * The fragment shader against matplotlib (runs in Chromium: `pnpm test`).
 *
 * `interpolation.reference.json` holds a 5 × 6 image upsampled 8 times by
 * matplotlib's resampler (`scripts/export_interpolation_reference.py`).
 */

import { afterEach, describe, expect, it } from 'vitest'

import { colormapLut, lutIndex } from '@/lib/colormaps'
import { scaleValue, type Scaling } from '@/lib/scaling'
import type { Interpolation } from '@/store/view'

import reference from './interpolation.reference.json'
import { ChromRenderer, type DrawParams } from './renderer'

const [ROWS, COLS] = reference.shape
const F = reference.factor
const W = COLS * F
const H = ROWS * F
const image = Float32Array.from(reference.image)
const layer = {
  data: image,
  rows: ROWS,
  cols: COLS,
  geometry: {
    rt1: { start: 0, step: 1, count: COLS },
    rt2: { start: 0, step: 1, count: ROWS },
  },
}

let renderer: ChromRenderer | null = null
afterEach(() => {
  renderer?.dispose()
  renderer = null
})

function render(params: Partial<DrawParams>, data = layer): Uint8Array {
  const canvas = document.createElement('canvas')
  renderer = new ChromRenderer(canvas)
  renderer.resize(W, H, 1)
  renderer.setLayer('overview', data)
  renderer.draw({
    domain: { x0: 0, x1: COLS, y0: 0, y1: ROWS },
    vmin: 0,
    vmax: 1,
    scaling: 'linear',
    k: 1000,
    interpolation: 'nearest',
    lut: colormapLut('gray'),
    shiftRows: 0,
    overlayOpacity: 0,
    ...params,
  })
  return renderer.readPixels()
}

/** Gray value of output pixel (x, y); y = 0 is the bottom row = data row 0. */
const gray = (pixels: Uint8Array, x: number, y: number) => pixels[(y * W + x) * 4]

describe('shader interpolation matches matplotlib', () => {
  for (const name of ['nearest', 'bilinear', 'bicubic'] as Interpolation[]) {
    it(name, () => {
      const pixels = render({ interpolation: name })
      const expected = reference.resampled[name]
      // matplotlib's gray table truncates (entry i can be i - 1): compare levels
      const lut = colormapLut('gray')
      // Agg resamples in fixed point; its edge handling differs, so compare
      // the interior for the smoothing filters
      const margin = name === 'nearest' ? 0 : 2 * F
      let worst = 0
      for (let y = margin; y < H - margin; y++) {
        for (let x = margin; x < W - margin; x++) {
          const want = lut[lutIndex(expected[y * W + x]) * 4]
          worst = Math.max(worst, Math.abs(gray(pixels, x, y) - want))
        }
      }
      expect(worst).toBeLessThanOrEqual(name === 'nearest' ? 0 : 2)
    })
  }
})

describe('shader scaling and colour map match the CPU functions', () => {
  for (const scaling of ['linear', 'sqrt', 'cbrt', 'log'] as Scaling[]) {
    it(scaling, () => {
      const params = { vmin: 0.1, vmax: 0.8, scaling, k: 300 }
      const lut = colormapLut('viridis')
      const pixels = render({ ...params, lut })
      for (let row = 0; row < ROWS; row++) {
        for (let col = 0; col < COLS; col++) {
          const i = lutIndex(scaleValue(image[row * COLS + col], params))
          const x = col * F + F / 2
          const y = row * F + F / 2
          const got = Array.from(pixels.subarray((y * W + x) * 4, (y * W + x) * 4 + 4))
          // float32 in the shader may land on a neighbouring entry at an edge
          const candidates = [i - 1, i, i + 1]
            .filter((j) => j >= 0 && j < 256)
            .map((j) => Array.from(lut.subarray(j * 4, j * 4 + 4)))
          expect(candidates).toContainEqual(got)
        }
      }
    })
  }
})

describe('shader details', () => {
  it('previews a phase shift as a cyclic roll of the rows', () => {
    const pixels = render({ shiftRows: 2 })
    const lut = colormapLut('gray')
    for (let row = 0; row < ROWS; row++) {
      const source = (row + 2) % ROWS // displayed row r shows row (r + s) mod n
      const want = lut[lutIndex(image[source * COLS]) * 4]
      expect(gray(pixels, F / 2, row * F + F / 2)).toBe(want)
    }
  })

  it('leaves NaN pixels and the outside transparent', () => {
    const data = Float32Array.from(image)
    data[0] = NaN
    const pixels = render({ domain: { x0: -1, x1: COLS, y0: 0, y1: ROWS } }, { ...layer, data })
    // x < 0 (outside the image) and the NaN pixel (column 0, row 0)
    const scale = (COLS + 1) / W
    const px = (col: number) => Math.floor((col + 1 + 0.5) / scale)
    expect(pixels[(2 * W + 1) * 4 + 3]).toBe(0)
    expect(pixels[(2 * W + px(0)) * 4 + 3]).toBe(0)
    expect(pixels[(2 * W + px(1)) * 4 + 3]).toBe(255)
  })

  it('draws the full-resolution detail over the pooled overview', () => {
    const canvas = document.createElement('canvas')
    renderer = new ChromRenderer(canvas)
    renderer.resize(W, H, 1)
    // overview: one pixel per 3 x 2 full pixels, all 0.25
    renderer.setLayer('overview', {
      data: new Float32Array(4).fill(0.25),
      rows: 2,
      cols: 2,
      geometry: { rt1: { start: 0, step: 3, count: 2 }, rt2: { start: 0, step: 2.5, count: 2 } },
    })
    // detail: columns 2-3, rows 1-2 at full resolution, all 0.75
    renderer.setLayer('detail', {
      data: new Float32Array(4).fill(0.75),
      rows: 2,
      cols: 2,
      geometry: { rt1: { start: 2, step: 1, count: 2 }, rt2: { start: 1, step: 1, count: 2 } },
    })
    const lut = colormapLut('gray')
    const base = {
      vmin: 0,
      vmax: 1,
      scaling: 'linear' as const,
      k: 1000,
      interpolation: 'nearest' as const,
      lut,
      overlayOpacity: 0,
      domain: { x0: 0, x1: COLS, y0: 0, y1: ROWS },
    }
    renderer.draw({ ...base, shiftRows: 0 })
    let pixels = renderer.readPixels()
    const at = (col: number, row: number) => gray(pixels, col * F + F / 2, row * F + F / 2)
    expect(at(2, 1)).toBe(lut[lutIndex(0.75) * 4])
    expect(at(3, 2)).toBe(lut[lutIndex(0.75) * 4])
    expect(at(1, 1)).toBe(lut[lutIndex(0.25) * 4])
    expect(at(4, 2)).toBe(lut[lutIndex(0.25) * 4])
    // a shift preview only rolls the overview (the detail lacks the wrapped rows)
    renderer.draw({ ...base, shiftRows: 1 })
    pixels = renderer.readPixels()
    expect(at(2, 1)).toBe(lut[lutIndex(0.25) * 4])
  })

  it('clips values outside the contrast range', () => {
    const data = new Float32Array(ROWS * COLS).fill(-5)
    data[1] = 7
    const pixels = render({}, { ...layer, data })
    expect(gray(pixels, F / 2, F / 2)).toBe(0)
    expect(gray(pixels, F + F / 2, F / 2)).toBe(255)
  })
})
