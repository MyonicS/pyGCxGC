/**
 * Images of the open documents (spec VIEW-1/2). An image is fetched once per
 * document revision. Images larger than the GPU budget are fetched max-pooled
 * as an overview; the visible region is then fetched at full resolution as a
 * detail layer.
 */

import { create } from 'zustand'

import type { ArrayHeader } from '@/api/arrays'
import type { ImageHeader } from '@/api/types'
import type { ImageGeometry } from '@/lib/geometry'
import { notifyError } from '@/lib/notify'
import { finiteRange } from '@/lib/stats'
import { maxTextureSize } from '@/chromatogram/renderer'

import { api, useSession } from './session'

/** Most pixels uploaded as one texture (spec VIEW-2: 4096 × 4096). */
export const PIXEL_BUDGET = 4096 * 4096

export interface Layer {
  data: Float32Array
  rows: number
  cols: number
  /** Geometry of the returned pixels. */
  geometry: ImageGeometry
  /** Pooling factors `[rows, cols]`. */
  pool: [number, number]
  /** `[row0, row1, col0, col1]` in full-resolution pixels. */
  region: [number, number, number, number]
}

export interface DocImage {
  docId: string
  revision: number
  signal: string
  mz: number[]
  /** Full-resolution grid. */
  full: ImageGeometry
  /** Phase shift of the document when the image was computed. */
  shift: number
  overview: Layer
  detail: Layer | null
  /** Finite `[min, max]` of the overview. */
  range: [number, number]
}

interface ImagesState {
  images: Record<string, DocImage | undefined>
  loading: Record<string, boolean>
  errors: Record<string, string | undefined>
}

export const useImages = create<ImagesState>(() => ({ images: {}, loading: {}, errors: {} }))

const set = useImages.setState
const get = useImages.getState

function layerOf(header: ImageHeader & ArrayHeader, data: Float32Array): Layer {
  return {
    data,
    rows: header.shape[0] as number,
    cols: header.shape[1] as number,
    geometry: { rt1: header.rt1, rt2: header.rt2 },
    pool: header.pool,
    region: header.region,
  }
}

let limitsOverride: { side: number; pixels: number } | null = null

/** Replace the device limits (tests). */
export function overrideTextureLimits(limits: { side: number; pixels: number } | null) {
  limitsOverride = limits
}

/** Largest texture side and pixel count this device can show at once. */
export function textureLimits() {
  if (limitsOverride) return limitsOverride
  const side = Math.min(maxTextureSize(), 16384)
  return { side, pixels: Math.min(PIXEL_BUDGET, side * side) }
}

export function fitsTexture(rows: number, cols: number): boolean {
  const { side, pixels } = textureLimits()
  return rows <= side && cols <= side && rows * cols <= pixels
}

const requests = new Map<string, number>()
let nextRequest = 1

/** Fetch the image of a document (replacing the current one when it arrives). */
export async function loadImage(docId: string): Promise<void> {
  const doc = useSession.getState().documents[docId]
  if (!doc) return
  const id = nextRequest++
  requests.set(docId, id)
  set((s) => ({ loading: { ...s.loading, [docId]: true } }))
  const [rows, cols] = doc.shape
  const { side, pixels } = textureLimits()
  const maxSize = fitsTexture(rows, cols)
    ? undefined
    : Math.min(side, Math.floor(Math.sqrt(pixels)))
  try {
    const { header, data } = await api().image(docId, { maxSize })
    if (requests.get(docId) !== id) return // superseded
    const overview = layerOf(header, data)
    // the full-resolution grid, exactly as the backend has it
    const g = header.grid
    const full: ImageGeometry = {
      rt1: { start: g.rt1_start, step: g.modulation_time / 60, count: g.n_mod },
      rt2: { start: g.rt2_start, step: g.sampling_interval, count: g.n_rt2 },
    }
    const current = useSession.getState().documents[docId]
    const image: DocImage = {
      docId,
      revision: header.revision,
      signal: header.signal,
      mz: header.mz,
      full,
      shift: Number(current?.params.shift ?? doc.params.shift ?? 0),
      overview,
      detail: null,
      range: finiteRange(data),
    }
    set((s) => ({
      images: { ...s.images, [docId]: image },
      loading: { ...s.loading, [docId]: false },
      errors: { ...s.errors, [docId]: undefined },
    }))
  } catch (err) {
    if (requests.get(docId) !== id) return
    set((s) => ({
      loading: { ...s.loading, [docId]: false },
      errors: { ...s.errors, [docId]: err instanceof Error ? err.message : String(err) },
    }))
    notifyError(`Image of ${doc.name}`, err)
  }
}

const detailRequests = new Map<string, { id: number; abort: AbortController }>()

/**
 * Fetch the visible full-resolution region `[r0, r1, c0, c1]` of a pooled
 * image (with a margin). Does nothing when the overview is not pooled or the
 * current detail layer already covers the region.
 */
export async function loadDetail(docId: string, region: [number, number, number, number]) {
  const image = get().images[docId]
  if (!image) return
  const [fr, fc] = image.overview.pool
  if (fr === 1 && fc === 1) return
  const [r0, r1, c0, c1] = region
  const covers = (l: Layer | null) =>
    l !== null &&
    l.pool[0] === 1 &&
    l.pool[1] === 1 &&
    l.region[0] <= r0 &&
    l.region[1] >= r1 &&
    l.region[2] <= c0 &&
    l.region[3] >= c1
  if (covers(image.detail)) return
  // the visible region is still too big for full resolution: the overview is enough
  if (!fitsTexture(r1 - r0, c1 - c0)) {
    if (image.detail) set((s) => ({ images: { ...s.images, [docId]: { ...image, detail: null } } }))
    return
  }
  // a margin of up to half the visible size on each side, within the budget
  const { pixels } = textureLimits()
  const grow = Math.min(0.5, (Math.sqrt(pixels / ((r1 - r0) * (c1 - c0))) - 1) / 2)
  const dr = Math.floor((r1 - r0) * grow)
  const dc = Math.floor((c1 - c0) * grow)
  const want: [number, number, number, number] = [
    Math.max(0, r0 - dr),
    Math.min(image.full.rt2.count, r1 + dr),
    Math.max(0, c0 - dc),
    Math.min(image.full.rt1.count, c1 + dc),
  ]
  detailRequests.get(docId)?.abort.abort()
  const id = nextRequest++
  const abort = new AbortController()
  detailRequests.set(docId, { id, abort })
  try {
    const { header, data } = await api().image(docId, { region: want, signal: abort.signal })
    const current = get().images[docId]
    if (detailRequests.get(docId)?.id !== id || !current) return
    if (header.revision !== current.revision) return // the overview is about to change
    set((s) => ({
      images: { ...s.images, [docId]: { ...current, detail: layerOf(header, data) } },
    }))
  } catch (err) {
    if ((err as Error).name !== 'AbortError') console.warn('Detail image', err)
  } finally {
    if (detailRequests.get(docId)?.id === id) detailRequests.delete(docId)
  }
}

export function forgetImage(docId: string) {
  requests.delete(docId)
  detailRequests.get(docId)?.abort.abort()
  detailRequests.delete(docId)
  set((s) => {
    const images = { ...s.images }
    delete images[docId]
    return { images }
  })
}
