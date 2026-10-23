/**
 * Provisional mask outlines (spec MASK-2: the frontend may draw them while
 * the user draws; Python's raster replaces them) and the handles of the
 * selected shape (MASK-6).
 */

import type { Shape } from '@/api/types'
import type { ViewTransform } from '@/lib/geometry'

import type { PlotRect } from './Axes'
import { screenPath, vertices, type Point } from './shapes'

export interface Draft {
  /** RT points: two corners (rect), the freehand path (lasso) or the vertices (polygon). */
  points: Point[]
  kind: 'rect' | 'lasso' | 'polygon'
  subtract: boolean
}

interface Props {
  plot: PlotRect
  transform: ViewTransform
  color: string
  draft: Draft | null
  /** Polygon rubber band end (RT). */
  cursor: Point | null
  /** Drawn shapes waiting for the server's raster. */
  pending: { shape: Shape; subtract: boolean }[]
  selected: { shape: Shape; color: string } | null
}

const HANDLE = 7

export function ShapeLayer({ plot, transform: t, color, draft, cursor, pending, selected }: Props) {
  return (
    <g transform={`translate(${plot.left} ${plot.top})`} data-testid="shape-layer">
      {pending.map((p, i) => (
        <Outline key={i} d={screenPath(vertices(p.shape), t)} color={color} subtract={p.subtract} />
      ))}
      {draft && <DraftShape draft={draft} cursor={cursor} t={t} color={color} />}
      {selected && (
        <g data-testid="selected-shape">
          <path
            d={screenPath(vertices(selected.shape), t)}
            fill="none"
            stroke={selected.color}
            strokeWidth={1.5}
            strokeDasharray="5 3"
          />
          {vertices(selected.shape).map(([a, b], i) => (
            <rect
              key={i}
              x={t.x(a) - HANDLE / 2}
              y={t.y(b) - HANDLE / 2}
              width={HANDLE}
              height={HANDLE}
              className="fill-plot-marker stroke-plot-halo"
              strokeWidth={1.5}
              data-testid="shape-handle"
            />
          ))}
        </g>
      )}
    </g>
  )
}

function DraftShape({
  draft,
  cursor,
  t,
  color,
}: {
  draft: Draft
  cursor: Point | null
  t: ViewTransform
  color: string
}) {
  const { points, kind, subtract } = draft
  if (kind === 'rect') {
    if (points.length < 2) return null
    const [a, b] = points
    const corners: Point[] = [a, [b[0], a[1]], b, [a[0], b[1]]]
    return <Outline d={screenPath(corners, t)} color={color} subtract={subtract} />
  }
  if (kind === 'lasso') {
    return <Outline d={screenPath(points, t)} color={color} subtract={subtract} />
  }
  // polygon: the edges so far, a rubber band to the cursor and the vertices
  const open = cursor ? [...points, cursor] : points
  return (
    <g>
      <Outline d={screenPath(open, t, points.length > 2)} color={color} subtract={subtract} />
      {points.map(([a, b], i) => (
        <circle
          key={i}
          cx={t.x(a)}
          cy={t.y(b)}
          r={i === 0 ? 4.5 : 3}
          className="fill-plot-marker stroke-plot-halo"
          strokeWidth={1.5}
        />
      ))}
    </g>
  )
}

function Outline({ d, color, subtract }: { d: string; color: string; subtract: boolean }) {
  return (
    <g>
      <path d={d} fill="none" className="stroke-plot-halo" strokeWidth={3} />
      <path
        d={d}
        fill={subtract ? 'none' : color}
        fillOpacity={0.15}
        stroke={color}
        strokeWidth={1.5}
        strokeDasharray={subtract ? '4 3' : undefined}
      />
    </g>
  )
}
