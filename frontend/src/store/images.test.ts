import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { encodeArray } from '@/api/arrays'
import type { RequestOptions, Transport } from '@/api/transport'
import { valueAt } from '@/chromatogram/imageMath'

import { loadDetail, loadImage, overrideTextureLimits, useImages } from './images'
import { connect } from './session'

// a 60 x 40 image with value 1000 * row + col
const ROWS = 60
const COLS = 40
const full = Float32Array.from(
  { length: ROWS * COLS },
  (_, i) => 1000 * Math.floor(i / COLS) + (i % COLS),
)
const doc = {
  id: 'd',
  kind: 'fid',
  name: 'big',
  path: null,
  revision: 1,
  params: { shift: 0 },
  shape: [ROWS, COLS],
  grid: {
    n_rt2: ROWS,
    n_mod: COLS,
    rt1_start: 0,
    rt2_start: 0,
    modulation_time: 6,
    sampling_interval: 0.1,
  },
  summary: '',
  warnings: [],
  n_masks: 0,
}

/** The image endpoint of pyGCxGC.app.server: region, then max pooling. */
function imageBody(query: RequestOptions['query'] = {}) {
  const [r0, r1, c0, c1] = query.region
    ? String(query.region).split(',').map(Number)
    : [0, ROWS, 0, COLS]
  const max = Number(query.max_size ?? Infinity)
  const rows = r1 - r0
  const cols = c1 - c0
  const fr = Math.max(1, Math.ceil(rows / max))
  const fc = Math.max(1, Math.ceil(cols / max))
  const pr = Math.ceil(rows / fr)
  const pc = Math.ceil(cols / fc)
  const out = new Float32Array(pr * pc).fill(-Infinity)
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++) {
      const i = Math.floor(r / fr) * pc + Math.floor(c / fc)
      out[i] = Math.max(out[i], full[(r0 + r) * COLS + c0 + c])
    }
  return encodeArray(out, [pr, pc], 'float32', {
    rt1: { start: c0 * 0.1, step: 0.1 * fc, count: pc },
    rt2: { start: r0 * 0.1, step: 0.1 * fr, count: pr },
    pool: [fr, fc],
    region: [r0, r1, c0, c1],
    full_shape: [ROWS, COLS],
    grid: doc.grid,
    revision: 1,
    signal: 'tic',
    mz: [],
  })
}

const requests: string[] = []
const transport: Transport = {
  async request<T>(_method: string, path: string, options: RequestOptions = {}) {
    if (path.endsWith('/image')) {
      requests.push(JSON.stringify(options.query))
      return imageBody(options.query) as T
    }
    if (path === '/api/session')
      return {
        version: 't',
        revision: 1,
        documents: [doc],
        libraries: [],
        jobs: [],
        settings: {},
      } as T
    return {
      document_id: 'd',
      revision: 0,
      can_undo: false,
      can_redo: false,
      masks: [],
      error: null,
    } as T
  },
  subscribe: () => () => undefined,
}

beforeAll(async () => {
  overrideTextureLimits({ side: 64, pixels: 400 })
  await connect(transport)
})
afterAll(() => overrideTextureLimits(null))

describe('large images (spec VIEW-2)', () => {
  it('fetches a max-pooled overview within the budget', async () => {
    await loadImage('d')
    expect(requests.at(-1)).toBe('{"max_size":20}')
    const image = useImages.getState().images.d!
    expect(image.overview.pool).toEqual([3, 2])
    expect([image.overview.rows, image.overview.cols]).toEqual([20, 20])
    expect(image.full.rt2).toEqual({ start: 0, step: 0.1, count: ROWS })
    expect(image.full.rt1).toEqual({ start: 0, step: 0.1, count: COLS })
    // the overview keeps peaks: block maxima
    expect(valueAt(image, 50, 30)).toEqual({ value: 50031, pooled: true })
  })

  it('fetches the visible region at full resolution, with a margin', async () => {
    await loadDetail('d', [10, 20, 5, 15])
    expect(requests.at(-1)).toBe('{"region":"5,25,0,20"}')
    const image = useImages.getState().images.d!
    expect(image.detail?.region).toEqual([5, 25, 0, 20])
    expect(image.detail?.pool).toEqual([1, 1])
    expect(valueAt(image, 12, 7)).toEqual({ value: 12007, pooled: false })
    expect(valueAt(image, 50, 30)?.pooled).toBe(true)
  })

  it('does not refetch a covered region and drops the detail when zoomed out', async () => {
    const n = requests.length
    await loadDetail('d', [12, 18, 3, 9])
    expect(requests.length).toBe(n)
    await loadDetail('d', [0, ROWS, 0, COLS])
    expect(requests.length).toBe(n)
    expect(useImages.getState().images.d?.detail).toBeNull()
  })
})
