/** SVG overlays (spec VIEW-11): mask outlines, peak markers and the selected point. */

import { memo } from 'react'

import type { SelectionInfo } from '@/api/types'
import { pixelCentre, ViewTransform, type ImageGeometry } from '@/lib/geometry'
import type { DocImage } from '@/store/images'

import type { PlotRect } from './Axes'
import type { MaskOutline } from './useMaskOverlay'

export interface PeakMarker {
  id: number
  /** Marker position (pixel centre). */
  rt1: number
  rt2: number
  name: string | null
  match: number | null
  rt1Min: number
  rt2S: number
}

/** Peak within `radius` px of a plot position, or `null`. */
export function nearestPeak(
  peaks: PeakMarker[],
  t: ViewTransform,
  px: number,
  py: number,
  radius: number,
): PeakMarker | null {
  let best: PeakMarker | null = null
  let bestD = radius * radius
  for (const p of peaks) {
    const dx = t.x(p.rt1) - px
    const dy = t.y(p.rt2) - py
    const d = dx * dx + dy * dy
    if (d <= bestD) {
      bestD = d
      best = p
    }
  }
  return best
}

/** Show labels only when few peaks are visible. */
const MAX_LABELS = 80

interface Props {
  plot: PlotRect
  transform: ViewTransform
  image: DocImage | null
  outlines: MaskOutline[]
  outlineGeometry: ImageGeometry | null
  peaks: PeakMarker[]
  hoverPeak: PeakMarker | null
  selection: SelectionInfo | null
}

function OverlaysImpl({
  plot,
  transform: t,
  image,
  outlines,
  outlineGeometry: g,
  peaks,
  hoverPeak,
  selection,
}: Props) {
  // pixel units -> screen: x = left + (start + col·step - x0) / span · width
  const maskMatrix = g
    ? [
        (g.rt1.step / (t.domain.x1 - t.domain.x0)) * t.width,
        0,
        0,
        -(g.rt2.step / (t.domain.y1 - t.domain.y0)) * t.height,
        plot.left + t.x(g.rt1.start),
        plot.top + t.y(g.rt2.start),
      ].join(' ')
    : null

  const visiblePeaks = peaks.filter((p) => {
    const x = t.x(p.rt1)
    const y = t.y(p.rt2)
    return x >= -10 && x <= t.width + 10 && y >= -10 && y <= t.height + 10
  })
  const labels = visiblePeaks.length <= MAX_LABELS

  let selected: { x: number; y: number } | null = null
  // a region spectrum (MS-11) has no point
  if (selection && image && selection.rt2_index >= 0 && selection.mod_index >= 0) {
    const c = pixelCentre(image.full, { row: selection.rt2_index, col: selection.mod_index })
    selected = { x: plot.left + t.x(c.rt1), y: plot.top + t.y(c.rt2) }
  }

  return (
    <g>
      {maskMatrix &&
        outlines.map((o) => (
          <g key={o.id} transform={`matrix(${maskMatrix})`}>
            <path
              d={o.path}
              fill="none"
              className="stroke-plot-halo"
              strokeWidth={3}
              vectorEffect="non-scaling-stroke"
            />
            <path
              d={o.path}
              fill="none"
              stroke={o.color}
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          </g>
        ))}
      {visiblePeaks.map((p) => {
        const x = plot.left + t.x(p.rt1)
        const y = plot.top + t.y(p.rt2)
        const hovered = hoverPeak?.id === p.id
        return (
          <g key={p.id}>
            <circle
              cx={x}
              cy={y}
              r={hovered ? 5 : 3.5}
              className="fill-none stroke-plot-halo"
              strokeWidth={3}
            />
            <circle
              cx={x}
              cy={y}
              r={hovered ? 5 : 3.5}
              className="fill-none stroke-plot-marker"
              strokeWidth={1.25}
            />
            {labels && (
              <text
                x={x + 6}
                y={y - 5}
                className="fill-plot-marker stroke-plot-halo text-[10px]"
                strokeWidth={2.5}
                paintOrder="stroke"
              >
                {p.name ? truncate(p.name, 22) : p.id}
              </text>
            )}
          </g>
        )
      })}
      {selected && (
        <g data-testid="selection-marker">
          {[3, 1.25].map((w, i) => (
            <g
              key={w}
              className={i ? 'stroke-plot-marker' : 'stroke-plot-halo'}
              strokeWidth={w}
              fill="none"
            >
              <circle cx={selected.x} cy={selected.y} r={7} />
              <line x1={selected.x - 12} x2={selected.x - 4} y1={selected.y} y2={selected.y} />
              <line x1={selected.x + 4} x2={selected.x + 12} y1={selected.y} y2={selected.y} />
              <line x1={selected.x} x2={selected.x} y1={selected.y - 12} y2={selected.y - 4} />
              <line x1={selected.x} x2={selected.x} y1={selected.y + 4} y2={selected.y + 12} />
            </g>
          ))}
        </g>
      )}
    </g>
  )
}

function truncate(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s
}

export const Overlays = memo(OverlaysImpl)
