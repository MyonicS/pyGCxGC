/**
 * Colour bar with ticks in data units, spaced by the current scaling (spec
 * VIEW-3), and a rotated label with the quantity and unit on the right.
 */

import { memo, useMemo } from 'react'

import { colormapLut, type ColormapName } from '@/lib/colormaps'
import type { ScaleParams } from '@/lib/scaling'
import { colorbarTicks } from '@/lib/ticks'

const imageCache = new Map<string, string>()

/** Room (px) right of the colour bar for the tick labels, before the label. */
export const COLORBAR_TICK_ROOM = 60

/** `"TIC (counts)"` from a document's value label. */
export function valueLabelText(
  value: { quantity: string; unit?: string | null } | null | undefined,
) {
  if (!value) return ''
  return value.unit ? `${value.quantity} (${value.unit})` : value.quantity
}

/** A 1 × 256 PNG of a colour map, index 0 at the bottom. */
export function colormapImage(name: ColormapName, reverse: boolean): string {
  const key = `${name}:${reverse}`
  let url = imageCache.get(key)
  if (!url) {
    const lut = colormapLut(name, reverse)
    const canvas = document.createElement('canvas')
    canvas.width = 1
    canvas.height = 256
    const ctx = canvas.getContext('2d')!
    const img = ctx.createImageData(1, 256)
    for (let i = 0; i < 256; i++) img.data.set(lut.subarray(i * 4, i * 4 + 4), (255 - i) * 4)
    ctx.putImageData(img, 0, 0)
    url = canvas.toDataURL('image/png')
    imageCache.set(key, url)
  }
  return url
}

interface Props {
  x: number
  top: number
  height: number
  width: number
  colormap: ColormapName
  reverse: boolean
  scale: ScaleParams
  /** Rotated label right of the ticks; nothing when empty. */
  label?: string
}

function ColorBarImpl({ x, top, height, width, colormap, reverse, scale, label }: Props) {
  const href = useMemo(() => colormapImage(colormap, reverse), [colormap, reverse])
  const ticks = colorbarTicks(scale, height)
  return (
    <g className="text-[11px]" data-testid="colorbar">
      <image
        href={href}
        x={x}
        y={top}
        width={width}
        height={height}
        preserveAspectRatio="none"
        style={{ imageRendering: 'pixelated' }}
      />
      <rect
        x={x - 0.5}
        y={top - 0.5}
        width={width + 1}
        height={height + 1}
        className="fill-none stroke-plot-axis"
      />
      {ticks.map((t) => {
        const y = Math.round(top + height - t.position * height) + 0.5
        return (
          <g key={t.value}>
            <line x1={x + width} x2={x + width + 4} y1={y} y2={y} className="stroke-plot-axis" />
            <text
              x={x + width + 7}
              y={y}
              dominantBaseline="middle"
              className="fill-plot-text tabular"
            >
              {t.label}
            </text>
          </g>
        )
      })}
      {label && (
        <text
          transform={`translate(${x + width + COLORBAR_TICK_ROOM},${top + height / 2}) rotate(-90)`}
          textAnchor="middle"
          dominantBaseline="middle"
          className="fill-plot-text"
          data-testid="colorbar-label"
        >
          {label}
        </text>
      )}
    </g>
  )
}

export const ColorBar = memo(ColorBarImpl)
