/**
 * Contrast histogram with draggable vmin / vmax handles (spec VIEW-6).
 * Bins are uniform in the current display scaling over the data range.
 */

import { useMemo, useRef, useState } from 'react'

import { colormapGradient, type ColormapName } from '@/lib/colormaps'
import { applyScaling, invertScaling, normalize, type Scaling } from '@/lib/scaling'
import { histogram } from '@/lib/stats'

const BINS = 120
const HEIGHT = 64
const HANDLE = 6

interface Props {
  data: Float32Array
  range: [number, number]
  vmin: number
  vmax: number
  scaling: Scaling
  k: number
  colormap: ColormapName
  reverse: boolean
  width: number
  onChange: (vmin: number, vmax: number) => void
}

export function Histogram({
  data,
  range,
  vmin,
  vmax,
  scaling,
  k,
  colormap,
  reverse,
  width,
  onChange,
}: Props) {
  const [lo, hi] = range
  const span = hi - lo
  // position in [0, 1] along the histogram <-> data value
  const toPos = (v: number) => applyScaling(normalize(v, lo, hi), scaling, k)
  const toValue = (p: number) => lo + invertScaling(Math.min(Math.max(p, 0), 1), scaling, k) * span

  const hist = useMemo(
    () =>
      histogram(
        data,
        BINS,
        lo,
        hi,
        (v) => applyScaling(normalize(v, lo, hi), scaling, k),
        (u) => lo + invertScaling(u, scaling, k) * (hi - lo),
      ),
    [data, lo, hi, scaling, k],
  )
  const logMax = Math.log1p(hist.max)
  const barW = width / BINS
  const svgRef = useRef<SVGSVGElement>(null)
  const [dragging, setDragging] = useState<'vmin' | 'vmax' | null>(null)

  const posOf = (clientX: number) => {
    const rect = svgRef.current!.getBoundingClientRect()
    return (clientX - rect.left) / rect.width
  }

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!(span > 0)) return
    const p = posOf(e.clientX)
    const which = Math.abs(p - toPos(vmin)) <= Math.abs(p - toPos(vmax)) ? 'vmin' : 'vmax'
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragging(which)
    move(which, p)
  }

  const move = (which: 'vmin' | 'vmax', p: number) => {
    const v = toValue(p)
    if (which === 'vmin') onChange(Math.min(v, vmax), vmax)
    else onChange(vmin, Math.max(v, vmin))
  }

  const xMin = toPos(vmin) * width
  const xMax = toPos(vmax) * width

  return (
    <div className="flex flex-col gap-1" data-testid="histogram">
      <svg
        ref={svgRef}
        width={width}
        height={HEIGHT}
        className="cursor-ew-resize touch-none rounded-sm bg-muted"
        onPointerDown={onPointerDown}
        onPointerMove={(e) => dragging && move(dragging, posOf(e.clientX))}
        onPointerUp={() => setDragging(null)}
        onPointerCancel={() => setDragging(null)}
      >
        <rect
          x={xMin}
          width={Math.max(0, xMax - xMin)}
          height={HEIGHT}
          className="fill-primary/10"
        />
        {Array.from(hist.counts, (c, i) => {
          const h = logMax > 0 ? (Math.log1p(c) / logMax) * (HEIGHT - 4) : 0
          return (
            <rect
              key={i}
              x={i * barW}
              y={HEIGHT - h}
              width={Math.max(barW - 0.5, 0.5)}
              height={h}
              className="fill-muted-foreground/60"
            />
          )
        })}
        {[xMin, xMax].map((x, i) => (
          <g key={i} className="fill-primary stroke-primary">
            <line x1={x} x2={x} y1={0} y2={HEIGHT} strokeWidth={1.5} />
            <path
              d={`M${x - HANDLE / 2} 0 L${x + HANDLE / 2} 0 L${x} ${HANDLE} Z`}
              strokeWidth={0}
            />
          </g>
        ))}
      </svg>
      <div
        className="h-2 rounded-sm"
        style={{
          width,
          backgroundImage: `${colormapGradient(colormap, reverse)}`,
          backgroundSize: `${Math.max(1, xMax - xMin)}px 100%`,
          backgroundPosition: `${xMin}px 0`,
          backgroundRepeat: 'no-repeat',
          backgroundColor: 'var(--muted)',
        }}
      />
    </div>
  )
}
