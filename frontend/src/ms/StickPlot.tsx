/**
 * Stick plot of mass spectra in SVG (spec §2: SVG + d3-scale). One spectrum
 * (MS-5), or the unknown up and a library spectrum down (head-to-tail,
 * MS-7). The most intense peaks are labelled with their m/z without
 * overlapping labels; hovering shows m/z and intensity. Colours are theme
 * tokens (THEME-2).
 */

import { scaleLinear } from 'd3-scale'
import { useMemo, useState } from 'react'

import {
  displayIntensity,
  maxOf,
  mzLimits,
  nearestStick,
  placeLabels,
  type Sticks,
} from '@/lib/spectrum'
import { formatValue, linearTicks } from '@/lib/ticks'
import { useElementSize } from '@/lib/useElementSize'
import { cn } from '@/lib/utils'

export interface Series {
  sticks: Sticks
  label: string
  /** Tailwind stroke/fill colour, e.g. `stroke-plot-unknown`. */
  stroke: string
  fill: string
}

interface Props {
  top: Series | null
  /** Plotted downwards (head-to-tail); intensities are then always relative. */
  bottom?: Series | null
  relative?: boolean
  maxLabels?: number
  empty?: React.ReactNode
  className?: string
  'data-testid'?: string
}

const MARGIN = { left: 46, right: 12, top: 4, bottom: 28 }
/** Head-to-tail plots have a legend strip above the plot. */
const LEGEND = 14
const LABEL_ROOM = 15

interface Hover {
  series: 'top' | 'bottom'
  index: number
  x: number
  y: number
}

export function StickPlot({
  top,
  bottom,
  relative = true,
  maxLabels = 10,
  empty,
  className,
  ...rest
}: Props) {
  const [ref, size] = useElementSize<HTMLDivElement>()
  const [hover, setHover] = useState<Hover | null>(null)
  const headToTail = bottom !== undefined
  const rel = headToTail || relative

  const plotW = Math.max(1, size.width - MARGIN.left - MARGIN.right)
  const marginTop = MARGIN.top + (headToTail ? LEGEND : 0)
  const plotH = Math.max(1, size.height - marginTop - MARGIN.bottom)

  const model = useMemo(() => {
    const spectra = [top?.sticks, bottom?.sticks].filter(Boolean) as Sticks[]
    const limits = mzLimits(spectra) ?? [40, 200]
    const x = scaleLinear().domain(limits).range([0, plotW])
    const topMax = top ? maxOf(top.sticks.intensity) : 0
    const bottomMax = bottom ? maxOf(bottom.sticks.intensity) : 0
    const yMaxValue = rel ? 999 : topMax || 1
    // fixed room (px) for the labels beyond the tallest sticks
    const y = scaleLinear()
      .domain(headToTail ? [-yMaxValue, yMaxValue] : [0, yMaxValue])
      .range([plotH - (headToTail ? LABEL_ROOM : 0), LABEL_ROOM])
    const series = (s: Series | null | undefined, max: number, sign: 1 | -1) => {
      if (!s) return null
      const n = s.sticks.mz.length
      const xs = new Float64Array(n)
      const ys = new Float64Array(n)
      const values = new Float64Array(n)
      for (let i = 0; i < n; i++) {
        values[i] = displayIntensity(s.sticks.intensity[i], max, rel)
        xs[i] = x(s.sticks.mz[i])
        ys[i] = y(sign * values[i])
      }
      return { s, xs, ys, values, max }
    }
    return {
      x,
      y,
      limits,
      top: series(top, topMax, 1),
      bottom: series(bottom, bottomMax, -1),
    }
  }, [top, bottom, rel, headToTail, plotW, plotH])

  const labels = useMemo(() => {
    const out: { key: string; x: number; y: number; text: string; cls: string }[] = []
    for (const [which, dir] of [
      ['top', -1],
      ['bottom', 1],
    ] as const) {
      const m = model[which]
      if (!m) continue
      const candidates = []
      for (let i = 0; i < m.xs.length; i++) {
        if (m.xs[i] < 0 || m.xs[i] > plotW) continue
        candidates.push({
          x: m.xs[i],
          y: m.ys[i],
          text: String(Math.round(m.s.sticks.mz[i])),
          priority: m.values[i],
        })
      }
      const placed = placeLabels(candidates, {
        width: plotW,
        maxLabels: headToTail ? Math.ceil(maxLabels * 0.7) : maxLabels,
        direction: dir,
      })
      for (const p of placed) {
        out.push({
          key: `${which}-${p.text}`,
          x: p.x,
          y: p.bottom - 2, // text baseline
          text: p.text,
          cls: m.s.fill,
        })
      }
    }
    return out
  }, [model, plotW, headToTail, maxLabels])

  const xTicks = linearTicks(model.limits[0], model.limits[1], plotW, 70)
  const [y0, y1] = model.y.domain()
  const yTicks = linearTicks(y0, y1, plotH, 40).filter((t) => Math.abs(t.value) <= (rel ? 999 : y1))

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const px = e.clientX - r.left
    const py = e.clientY - r.top
    const inBottom = Boolean(model.bottom) && py > model.y(0)
    const which = inBottom ? 'bottom' : 'top'
    const m = model[which]
    const index = m ? nearestStick(m.xs, px, 6) : -1
    setHover(m && index >= 0 ? { series: which, index, x: m.xs[index], y: m.ys[index] } : null)
  }

  const hovered = hover ? model[hover.series] : null
  const hasData = Boolean(model.top?.xs.length || model.bottom?.xs.length)

  return (
    <div ref={ref} className={cn('relative h-full w-full overflow-hidden', className)} {...rest}>
      {!hasData && empty && (
        <div className="absolute inset-0 flex items-center justify-center p-4 text-center text-xs text-muted-foreground">
          {empty}
        </div>
      )}
      {size.width > 0 && size.height > 0 && hasData && (
        <svg width={size.width} height={size.height} className="block select-none">
          <g transform={`translate(${MARGIN.left},${marginTop})`}>
            {/* grid and axes */}
            {yTicks.map((t) => (
              <line
                key={`gy${t.value}`}
                x1={0}
                x2={plotW}
                y1={model.y(t.value)}
                y2={model.y(t.value)}
                className="stroke-plot-grid"
              />
            ))}
            <line x1={0} x2={0} y1={0} y2={plotH} className="stroke-plot-axis" />
            <line x1={0} x2={plotW} y1={plotH} y2={plotH} className="stroke-plot-axis" />
            {headToTail && (
              <line
                x1={0}
                x2={plotW}
                y1={model.y(0)}
                y2={model.y(0)}
                className="stroke-plot-axis"
              />
            )}
            {xTicks.map((t) => (
              <g key={`x${t.value}`} transform={`translate(${model.x(t.value)},${plotH})`}>
                <line y2={4} className="stroke-plot-axis" />
                <text y={15} textAnchor="middle" className="fill-plot-text text-[10px] tabular">
                  {t.label}
                </text>
              </g>
            ))}
            <text
              x={plotW}
              y={plotH + 25}
              textAnchor="end"
              className="fill-plot-text text-[10px] italic"
            >
              m/z
            </text>
            {yTicks.map((t) => (
              <g key={`y${t.value}`} transform={`translate(0,${model.y(t.value)})`}>
                <line x2={-4} className="stroke-plot-axis" />
                <text
                  x={-6}
                  dy="0.32em"
                  textAnchor="end"
                  className="fill-plot-text text-[10px] tabular"
                >
                  {rel ? Math.abs(t.value) : formatValue(t.value)}
                </text>
              </g>
            ))}

            {/* sticks */}
            <svg width={plotW} height={plotH} overflow="hidden">
              {(['top', 'bottom'] as const).map((which) => {
                const m = model[which]
                if (!m) return null
                const base = model.y(0)
                let d = ''
                for (let i = 0; i < m.xs.length; i++) {
                  d += `M${m.xs[i].toFixed(2)} ${base.toFixed(2)}V${m.ys[i].toFixed(2)}`
                }
                return (
                  <path
                    key={which}
                    d={d}
                    className={m.s.stroke}
                    strokeWidth={1.4}
                    data-testid={`sticks-${which}`}
                  />
                )
              })}
              {labels.map((l) => (
                <text
                  key={l.key}
                  x={l.x}
                  y={l.y}
                  textAnchor="middle"
                  className={cn('text-[10px] tabular', l.cls)}
                >
                  {l.text}
                </text>
              ))}
              {hover && hovered && (
                <line
                  data-export-skip=""
                  x1={hover.x}
                  x2={hover.x}
                  y1={model.y(0)}
                  y2={hover.y}
                  className="stroke-foreground"
                  strokeWidth={2.5}
                />
              )}
            </svg>

            {headToTail && (
              <text x={plotW} y={-5} textAnchor="end" className="text-[10px]">
                {top && <tspan className={top.fill}>▲ {top.label}</tspan>}
                {bottom && (
                  <tspan dx={10} className={bottom.fill}>
                    ▼ {bottom.label}
                  </tspan>
                )}
              </text>
            )}
            <rect
              data-export-skip=""
              width={plotW}
              height={plotH}
              fill="transparent"
              onPointerMove={onMove}
              onPointerLeave={() => setHover(null)}
            />
          </g>
        </svg>
      )}
      {hover && hovered && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border bg-popover px-2 py-1 text-[11px] text-popover-foreground shadow-md"
          style={{
            left: Math.min(MARGIN.left + hover.x + 10, size.width - 120),
            top: Math.max(marginTop + Math.min(hover.y, model.y(0)) - 34, 2),
          }}
          data-testid="stick-tooltip"
        >
          <div className="font-medium">
            m/z {Math.round(hovered.s.sticks.mz[hover.index])}
            <span className="ml-1.5 font-normal text-muted-foreground">{hovered.s.label}</span>
          </div>
          <div className="font-mono tabular">
            {rel ? `${Math.round(hovered.values[hover.index])} rel. · ` : ''}
            {formatValue(hovered.s.sticks.intensity[hover.index], 4)}
          </div>
        </div>
      )}
    </div>
  )
}
