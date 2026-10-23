import { useEffect, useState } from 'react'

import type { MaskInfo, MaskSetInfo } from '@/api/types'
import type { ImageGeometry } from '@/lib/geometry'
import { api } from '@/store/session'

import { composeMasks, outlinePath } from './masks'
import type { OverlayLayer } from './renderer'

export interface MaskOutline {
  id: string
  name: string
  color: string
  /** Outline in pixel units of {@link MaskOverlay.geometry}. */
  path: string
}

export interface MaskOverlay {
  layer: OverlayLayer | null
  outlines: MaskOutline[]
  geometry: ImageGeometry | null
}

const EMPTY: MaskOverlay = { layer: null, outlines: [], geometry: null }

interface CachedRaster {
  data: Uint8Array
  rows: number
  cols: number
  geometry: ImageGeometry
  outline: string
}

/**
 * Rasters by mask content: a mask's pixels only change with its operations
 * or the document (grid, processing), so an edit refetches one raster.
 */
const rasters = new Map<string, Promise<CachedRaster>>()
const MAX_CACHED = 64

function rasterKey(docId: string, docRevision: number | undefined, m: MaskInfo) {
  return `${docId}|${docRevision}|${m.id}|${JSON.stringify(m.operations)}`
}

function fetchRaster(key: string, docId: string, maskId: string): Promise<CachedRaster> {
  let entry = rasters.get(key)
  if (!entry) {
    entry = api()
      .maskRaster(docId, maskId)
      .then(({ header, data }) => {
        const [rows, cols] = header.shape as [number, number]
        const raster = data as Uint8Array
        return {
          data: raster,
          rows,
          cols,
          geometry: { rt1: header.rt1, rt2: header.rt2 },
          outline: outlinePath(raster, rows, cols),
        }
      })
    entry.catch(() => rasters.delete(key))
    rasters.set(key, entry)
    while (rasters.size > MAX_CACHED) rasters.delete(rasters.keys().next().value!)
  }
  return entry
}

/**
 * Rasters of the visible masks of a document (from Python, spec MASK-2),
 * combined into one overlay texture plus outlines.
 */
export function useMaskOverlay(
  docId: string | undefined,
  masks: MaskSetInfo | undefined,
  docRevision: number | undefined,
): MaskOverlay {
  const [result, setResult] = useState<{ key: string; overlay: MaskOverlay } | null>(null)
  const visible = (masks?.masks ?? []).filter((m) => m.visible && m.operations.length > 0)
  const keys = docId ? visible.map((m) => rasterKey(docId, docRevision, m)) : []
  // colours are applied here, not in the cached rasters
  const key = `${docId}|${keys.join('#')}|${visible.map((m) => m.color).join(',')}`

  useEffect(() => {
    if (!docId || !visible.length) return
    let cancelled = false
    void (async () => {
      let overlay = EMPTY
      try {
        const items = await Promise.all(visible.map((m, i) => fetchRaster(keys[i], docId, m.id)))
        const { rows, cols, geometry } = items[0]
        overlay = {
          layer: {
            rgba: composeMasks(
              items.map((r, i) => ({ id: visible[i].id, color: visible[i].color, data: r.data })),
              rows,
              cols,
            ),
            rows,
            cols,
            geometry,
          },
          outlines: items.map((r, i) => ({
            id: visible[i].id,
            name: visible[i].name,
            color: visible[i].color,
            path: r.outline,
          })),
          geometry,
        }
      } catch (err) {
        console.warn('Mask overlay', err)
      }
      if (!cancelled) setResult({ key, overlay })
    })()
    return () => {
      cancelled = true
    }
    // `visible` and `keys` are derived from `masks`; `key` captures their changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (!visible.length) return EMPTY
  // keep showing the previous overlay of this document until the new one is ready
  return result && result.key.startsWith(`${docId}|`) ? result.overlay : EMPTY
}
