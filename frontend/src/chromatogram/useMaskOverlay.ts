import { useEffect, useState } from 'react'

import type { MaskSetInfo } from '@/api/types'
import type { ImageGeometry } from '@/lib/geometry'
import { api } from '@/store/session'

import { composeMasks, outlinePath, type MaskRaster } from './masks'
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

/**
 * Rasters of the visible masks of a document (fetched from Python whenever
 * the masks or the document change), combined for the overlay.
 */
export function useMaskOverlay(
  docId: string | undefined,
  masks: MaskSetInfo | undefined,
  docRevision: number | undefined,
): MaskOverlay {
  const [result, setResult] = useState<{ key: string; overlay: MaskOverlay } | null>(null)
  const visible = (masks?.masks ?? []).filter((m) => m.visible && m.operations.length > 0)
  const key = `${docId}|${masks?.revision}|${docRevision}|${visible
    .map((m) => `${m.id}:${m.color}`)
    .join(',')}`

  useEffect(() => {
    if (!docId || !visible.length) return
    let cancelled = false
    void (async () => {
      let overlay = EMPTY
      try {
        const rasters = await Promise.all(visible.map((m) => api().maskRaster(docId, m.id)))
        const { header } = rasters[0]
        const [rows, cols] = header.shape as [number, number]
        const items: MaskRaster[] = rasters.map((r, i) => ({
          id: visible[i].id,
          color: visible[i].color,
          data: r.data as Uint8Array,
        }))
        const geometry = { rt1: header.rt1, rt2: header.rt2 }
        overlay = {
          layer: { rgba: composeMasks(items, rows, cols), rows, cols, geometry },
          outlines: items.map((m, i) => ({
            id: m.id,
            name: visible[i].name,
            color: m.color,
            path: outlinePath(m.data, rows, cols),
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
    // `visible` is derived from `masks`; `key` captures its changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (!visible.length) return EMPTY
  // keep showing the previous overlay of this document until the new one is ready
  return result && result.key.startsWith(`${docId}|`) ? result.overlay : EMPTY
}
