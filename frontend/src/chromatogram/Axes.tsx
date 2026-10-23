/** Axes of the chromatogram (spec VIEW-3): RT1 (min) on x, RT2 (s) on y, upwards. */

import { memo } from 'react'

import type { Domain } from '@/lib/geometry'
import { linearTicks } from '@/lib/ticks'

export interface PlotRect {
  left: number
  top: number
  width: number
  height: number
}

const TICK = 5

function AxesImpl({ plot, domain }: { plot: PlotRect; domain: Domain }) {
  const xTicks = linearTicks(domain.x0, domain.x1, plot.width, 90)
  const yTicks = linearTicks(domain.y0, domain.y1, plot.height, 48)
  const bottom = plot.top + plot.height
  return (
    <g className="text-[11px]" data-testid="axes">
      <rect
        x={plot.left - 0.5}
        y={plot.top - 0.5}
        width={plot.width + 1}
        height={plot.height + 1}
        className="fill-none stroke-plot-axis"
      />
      <g data-testid="x-ticks">
        {xTicks.map((t) => {
          const x = Math.round(plot.left + t.position * plot.width) + 0.5
          return (
            <g key={t.value}>
              <line x1={x} x2={x} y1={bottom} y2={bottom + TICK} className="stroke-plot-axis" />
              <text
                x={x}
                y={bottom + TICK + 12}
                textAnchor="middle"
                className="fill-plot-text tabular"
              >
                {t.label}
              </text>
            </g>
          )
        })}
      </g>
      <g data-testid="y-ticks">
        {yTicks.map((t) => {
          const y = Math.round(bottom - t.position * plot.height) + 0.5
          return (
            <g key={t.value}>
              <line
                x1={plot.left - TICK}
                x2={plot.left}
                y1={y}
                y2={y}
                className="stroke-plot-axis"
              />
              <text
                x={plot.left - TICK - 3}
                y={y}
                textAnchor="end"
                dominantBaseline="middle"
                className="fill-plot-text tabular"
              >
                {t.label}
              </text>
            </g>
          )
        })}
      </g>
      <text
        x={plot.left + plot.width / 2}
        y={bottom + 36}
        textAnchor="middle"
        className="fill-plot-text text-xs"
      >
        <tspan>¹t</tspan>
        <tspan dy="3" fontSize="9">
          R
        </tspan>
        <tspan dy="-3"> (min)</tspan>
      </text>
      <text
        transform={`translate(${plot.left - 44} ${plot.top + plot.height / 2}) rotate(-90)`}
        textAnchor="middle"
        className="fill-plot-text text-xs"
      >
        <tspan>²t</tspan>
        <tspan dy="3" fontSize="9">
          R
        </tspan>
        <tspan dy="-3"> (s)</tspan>
      </text>
    </g>
  )
}

export const Axes = memo(AxesImpl)
