/** Lookups on displayed images: cursor values, visible regions, auto contrast. */

import type { DocumentInfo } from '@/api/types'
import { indexRange, type Domain, type ImageGeometry } from '@/lib/geometry'
import { finiteValues, percentileInPlace } from '@/lib/stats'
import type { DocImage, Layer } from '@/store/images'

/** Visible full-resolution pixels `[row0, row1, col0, col1]` (half-open). */
export function visibleRegion(full: ImageGeometry, d: Domain): [number, number, number, number] {
  const [c0, c1] = indexRange(full.rt1, d.x0, d.x1)
  const [r0, r1] = indexRange(full.rt2, d.y0, d.y1)
  return [r0, r1, c0, c1]
}

function inLayer(l: Layer, row: number, col: number) {
  return l.region[0] <= row && row < l.region[1] && l.region[2] <= col && col < l.region[3]
}

/**
 * Displayed value of a full-resolution pixel: from the detail layer when it
 * holds the pixel, else from the (possibly pooled) overview. `shiftRows` is
 * the phase-shift preview in overview rows.
 */
export function valueAt(
  image: DocImage,
  row: number,
  col: number,
  shiftRows = 0,
): { value: number; pooled: boolean } | null {
  const { detail, overview } = image
  if (detail && shiftRows === 0 && inLayer(detail, row, col)) {
    const [fr, fc] = detail.pool
    const r = Math.floor((row - detail.region[0]) / fr)
    const c = Math.floor((col - detail.region[2]) / fc)
    return { value: detail.data[r * detail.cols + c], pooled: fr > 1 || fc > 1 }
  }
  const [fr, fc] = overview.pool
  let r = Math.floor((row - overview.region[0]) / fr)
  const c = Math.floor((col - overview.region[2]) / fc)
  if (r < 0 || r >= overview.rows || c < 0 || c >= overview.cols) return null
  if (shiftRows) r = (((r + shiftRows) % overview.rows) + overview.rows) % overview.rows
  return { value: overview.data[r * overview.cols + c], pooled: fr > 1 || fc > 1 }
}

/**
 * `vmax` of the automatic contrast (spec VIEW-6): the 99.9th percentile of
 * the visible data (of the whole image without a domain). Falls back to the
 * maximum when the percentile is not above 0.
 */
export function autoVmax(image: DocImage, domain?: Domain): number {
  const layer =
    domain && image.detail && covers(image.detail, visibleRegion(image.full, domain))
      ? image.detail
      : image.overview
  let region = { r0: 0, r1: layer.rows, c0: 0, c1: layer.cols }
  if (domain) {
    const [r0, r1, c0, c1] = visibleRegion(layer.geometry, domain)
    if (r1 > r0 && c1 > c0) region = { r0, r1, c0, c1 }
  }
  const values = finiteValues(layer.data, layer.cols, region)
  let vmax = percentileInPlace(values, 99.9)
  if (!(vmax > 0)) {
    vmax = -Infinity
    for (let i = 0; i < values.length; i++) if (values[i] > vmax) vmax = values[i]
  }
  return vmax > 0 ? vmax : 1
}

function covers(l: Layer, [r0, r1, c0, c1]: [number, number, number, number]) {
  return l.region[0] <= r0 && l.region[1] >= r1 && l.region[2] <= c0 && l.region[3] >= c1
}

/**
 * Scan number of a pixel of a GCxGC-MS document (`mod · n_rt2 + row + shift`,
 * as `GCxGC_MS.scan_at`), or `null` outside the acquisition.
 */
export function scanAt(doc: DocumentInfo, row: number, col: number): number | null {
  if (doc.kind !== 'ms' || doc.n_scans == null) return null
  const scan = col * doc.grid.n_rt2 + row + Number(doc.params.shift ?? 0)
  return scan >= 0 && scan < doc.n_scans ? scan : null
}
