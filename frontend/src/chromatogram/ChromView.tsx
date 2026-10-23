/**
 * The 2D chromatogram view (spec §5): WebGL image, axes, colour bar,
 * overlays, crosshair and the select / pan / zoom-box interactions.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import type { DocumentInfo } from '@/api/types'
import { colormapLut } from '@/lib/colormaps'
import {
  constrainDomain,
  fullDomain,
  panDomain,
  pixelAt,
  spanX,
  spanY,
  ViewTransform,
  zoomDomain,
  type Domain,
} from '@/lib/geometry'
import { notifyError } from '@/lib/notify'
import { formatValue } from '@/lib/ticks'
import { debounce } from '@/lib/utils'
import { loadDetail, loadImage, useImages } from '@/store/images'
import { api, useSession } from '@/store/session'
import {
  defaultDisplay,
  effectiveContrast,
  ensureDisplay,
  resetZoom,
  setCursor,
  setDomain,
  updateDisplay,
  useView,
  type Cursor,
} from '@/store/view'

import { Axes, type PlotRect } from './Axes'
import { ColorBar } from './ColorBar'
import { autoVmax, scanAt, valueAt, visibleRegion } from './imageMath'
import { Overlays, nearestPeak, type PeakMarker } from './Overlays'
import { ChromRenderer, WebGLUnavailableError } from './renderer'
import { useMaskOverlay } from './useMaskOverlay'

const MARGIN = { left: 62, right: 90, top: 12, bottom: 44 }
const COLORBAR = { gap: 14, width: 14 }
/** Smallest visible span, in pixels of the full-resolution grid. */
const MIN_PIXELS = 1.5
/** Pointer travel (px) that turns a click into a drag. */
const DRAG_THRESHOLD = 4

type Drag =
  | { kind: 'pan'; pointerId: number; x: number; y: number; domain: Domain }
  | { kind: 'zoom'; pointerId: number; x0: number; y0: number; x: number; y: number }
  | { kind: 'click'; pointerId: number; x: number; y: number; domain: Domain }

export function ChromView({ doc }: { doc: DocumentInfo }) {
  const docId = doc.id
  const containerRef = useRef<HTMLDivElement>(null)
  const rendererRef = useRef<ChromRenderer | null>(null)
  const [glError, setGlError] = useState<string | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [drag, setDrag] = useState<Drag | null>(null)
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null)
  const [hoverPeak, setHoverPeak] = useState<PeakMarker | null>(null)
  const [redraw, setRedraw] = useState(0)

  const settings = useSession((s) => s.settings)
  const masks = useSession((s) => s.masks[docId])
  const selection = useSession((s) => s.selections[docId])
  const peaks = useSession((s) => s.peaks[docId])
  const image = useImages((s) => s.images[docId])
  const loading = useImages((s) => s.loading[docId])
  const imageError = useImages((s) => s.errors[docId])
  const storedDisplay = useView((s) => s.display[docId])
  const display = useMemo(
    () => storedDisplay ?? defaultDisplay(settings),
    [storedDisplay, settings],
  )
  const lock = useView((s) => s.lock)
  const tool = useView((s) => s.tool)
  const storedDomain = useView((s) => s.domains[docId])
  const shiftPreview = useView((s) => s.shiftPreview[docId])
  const overlay = useMaskOverlay(docId, masks, doc.revision)

  // ------------------------------------------------------------ data
  useEffect(() => {
    ensureDisplay(docId, settings)
  }, [docId, settings])

  useEffect(() => {
    void loadImage(docId)
  }, [docId, doc.revision])

  // automatic contrast for new images
  useEffect(() => {
    if (!image || display.contrast !== 'auto') return
    updateDisplay(docId, { vmin: 0, vmax: autoVmax(image) })
    // only when the image changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [image?.overview])

  const full = image?.full
  const fullExtent = useMemo(() => (full ? fullDomain(full) : null), [full])
  const domain = useMemo(() => {
    if (!fullExtent || !full) return null
    if (!storedDomain) return fullExtent
    return constrainDomain(
      storedDomain,
      fullExtent,
      MIN_PIXELS * full.rt1.step,
      MIN_PIXELS * full.rt2.step,
    )
  }, [storedDomain, fullExtent, full])
  // latest domain for event handlers (also updated by them before the store re-renders)
  const domainRef = useRef<Domain | null>(domain)
  useLayoutEffect(() => {
    domainRef.current = domain
  }, [domain])

  const plot: PlotRect = useMemo(
    () => ({
      left: MARGIN.left,
      top: MARGIN.top,
      width: Math.max(1, size.width - MARGIN.left - MARGIN.right),
      height: Math.max(1, size.height - MARGIN.top - MARGIN.bottom),
    }),
    [size],
  )
  const transform = useMemo(
    () => (domain ? new ViewTransform(domain, plot.width, plot.height) : null),
    [domain, plot],
  )

  // full-resolution detail of pooled images (spec VIEW-2)
  const requestDetail = useMemo(
    () =>
      debounce((id: string, region: [number, number, number, number]) => {
        void loadDetail(id, region)
      }, 150),
    [],
  )
  useEffect(() => {
    if (!image || !domain) return
    if (image.overview.pool[0] === 1 && image.overview.pool[1] === 1) return
    requestDetail(docId, visibleRegion(image.full, domain))
  }, [docId, image, domain, requestDetail])
  useEffect(() => () => requestDetail.cancel(), [requestDetail])

  // ------------------------------------------------------------ renderer
  const canvasRef = useCallback((canvas: HTMLCanvasElement | null) => {
    if (!canvas) return
    let renderer: ChromRenderer
    try {
      renderer = new ChromRenderer(canvas)
    } catch (err) {
      setGlError(err instanceof WebGLUnavailableError ? err.message : String(err))
      return
    }
    renderer.onRestore = () => setRedraw((n) => n + 1)
    rendererRef.current = renderer
    setRedraw((n) => n + 1)
    return () => {
      renderer.dispose()
      rendererRef.current = null
    }
  }, [])

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setSize({ width: Math.floor(width), height: Math.floor(height) })
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const contrast = effectiveContrast(display, lock)
  const lut = colormapLut(display.colormap, display.reverse)
  const shiftRows =
    image && shiftPreview !== undefined
      ? Math.round((shiftPreview - image.shift) / image.overview.pool[0])
      : 0

  useLayoutEffect(() => {
    const renderer = rendererRef.current
    if (!renderer || !image || !domain) return
    renderer.resize(plot.width, plot.height)
    renderer.setLayer('overview', image.overview)
    renderer.setLayer('detail', image.detail)
    renderer.setOverlay(display.showMasks ? overlay.layer : null)
    renderer.draw({
      domain,
      vmin: contrast.vmin,
      vmax: contrast.vmax,
      scaling: display.scaling,
      k: display.k,
      interpolation: display.interpolation,
      lut,
      shiftRows,
      overlayOpacity: display.showMasks && overlay.layer ? display.maskOpacity : 0,
    })
  }, [
    image,
    domain,
    plot,
    contrast.vmin,
    contrast.vmax,
    display.scaling,
    display.k,
    display.interpolation,
    display.showMasks,
    display.maskOpacity,
    lut,
    shiftRows,
    overlay.layer,
    redraw,
  ])

  // ------------------------------------------------------------ interaction
  const applyDomain = useCallback(
    (d: Domain) => {
      if (!fullExtent || !full) return
      const next = constrainDomain(
        d,
        fullExtent,
        MIN_PIXELS * full.rt1.step,
        MIN_PIXELS * full.rt2.step,
      )
      domainRef.current = next
      setDomain(docId, next)
    },
    [docId, fullExtent, full],
  )

  const local = (e: { clientX: number; clientY: number }) => {
    const rect = containerRef.current!.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }
  const inPlot = (x: number, y: number) =>
    x >= plot.left && x <= plot.left + plot.width && y >= plot.top && y <= plot.top + plot.height

  // wheel: zoom about the cursor; over an axis only that axis (spec VIEW-9)
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      const d = domainRef.current
      if (!d) return
      const rect = el.getBoundingClientRect()
      const x = e.clientX - rect.left - plot.left
      const y = e.clientY - rect.top - plot.top
      const overX = y > plot.height && x >= 0 && x <= plot.width
      const overY = x < 0 && y >= 0 && y <= plot.height
      if (!overX && !overY && !(x >= 0 && x <= plot.width && y >= 0 && y <= plot.height)) return
      e.preventDefault()
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? plot.height : 1
      const factor = Math.exp(e.deltaY * unit * 0.0015)
      const t = new ViewTransform(d, plot.width, plot.height)
      const ax = t.rt1(Math.min(Math.max(x, 0), plot.width))
      const ay = t.rt2(Math.min(Math.max(y, 0), plot.height))
      applyDomain(zoomDomain(d, ax, ay, overY ? 1 : factor, overX ? 1 : factor))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [plot, applyDomain])

  const updateCursor = (x: number, y: number) => {
    if (!image || !transform || !inPlot(x, y)) {
      if (useView.getState().cursor?.docId === docId) setCursor(null)
      return
    }
    const rt1 = transform.rt1(x - plot.left)
    const rt2 = transform.rt2(y - plot.top)
    const pixel = pixelAt(image.full, rt1, rt2)
    if (!pixel) {
      setCursor(null)
      return
    }
    const v = valueAt(image, pixel.row, pixel.col, shiftRows)
    const cursor: Cursor = {
      docId,
      rt1,
      rt2,
      row: pixel.row,
      col: pixel.col,
      value: v ? v.value : null,
      pooled: v?.pooled ?? false,
      scan: scanAt(doc, pixel.row, pixel.col),
    }
    setCursor(cursor)
  }

  const peakMarkers: PeakMarker[] = useMemo(() => {
    if (!peaks || !image) return []
    const { rt1, rt2 } = image.full
    return peaks.rows.map((row) => {
      const col = Number(row.mod_index)
      const r = Number(row.rt2_index)
      return {
        id: Number(row.peak_id),
        rt1: rt1.start + (col + 0.5) * rt1.step,
        rt2: rt2.start + (r + 0.5) * rt2.step,
        name: typeof row.name === 'string' ? row.name : null,
        match: typeof row.match === 'number' ? row.match : null,
        rt1Min: Number(row.rt1_min),
        rt2S: Number(row.rt2_s),
      }
    })
  }, [peaks, image])

  const onPointerDown = (e: React.PointerEvent) => {
    if (!domain) return
    const { x, y } = local(e)
    if (!inPlot(x, y)) return
    const middle = e.button === 1
    if (e.button !== 0 && !middle) return
    e.preventDefault()
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    if (middle || tool === 'pan') setDrag({ kind: 'pan', pointerId: e.pointerId, x, y, domain })
    else if (tool === 'zoom') setDrag({ kind: 'zoom', pointerId: e.pointerId, x0: x, y0: y, x, y })
    else setDrag({ kind: 'click', pointerId: e.pointerId, x, y, domain })
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const { x, y } = local(e)
    setHover(inPlot(x, y) ? { x, y } : null)
    updateCursor(x, y)
    if (transform && !drag) {
      setHoverPeak(
        display.showPeaks && inPlot(x, y)
          ? nearestPeak(peakMarkers, transform, x - plot.left, y - plot.top, 7)
          : null,
      )
    }
    if (!drag || drag.pointerId !== e.pointerId) return
    if (drag.kind === 'zoom') {
      const cx = Math.min(Math.max(x, plot.left), plot.left + plot.width)
      const cy = Math.min(Math.max(y, plot.top), plot.top + plot.height)
      setDrag({ ...drag, x: cx, y: cy })
      return
    }
    const dx = x - drag.x
    const dy = y - drag.y
    if (drag.kind === 'click' && Math.hypot(dx, dy) < DRAG_THRESHOLD) return
    // a drag in the select tool pans
    const start = drag.domain
    applyDomain(
      panDomain(start, (-dx / plot.width) * spanX(start), (dy / plot.height) * spanY(start)),
    )
    if (drag.kind === 'click') setDrag({ ...drag, kind: 'pan' })
  }

  const onPointerUp = (e: React.PointerEvent) => {
    if (!drag || drag.pointerId !== e.pointerId) return
    setDrag(null)
    if (drag.kind === 'zoom' && transform) {
      const w = Math.abs(drag.x - drag.x0)
      const h = Math.abs(drag.y - drag.y0)
      if (w < DRAG_THRESHOLD && h < DRAG_THRESHOLD) return
      const xs = [drag.x0, drag.x].map((v) => transform.rt1(v - plot.left))
      const ys = [drag.y0, drag.y].map((v) => transform.rt2(v - plot.top))
      const d = domainRef.current!
      applyDomain({
        x0: w < DRAG_THRESHOLD ? d.x0 : Math.min(...xs),
        x1: w < DRAG_THRESHOLD ? d.x1 : Math.max(...xs),
        y0: h < DRAG_THRESHOLD ? d.y0 : Math.min(...ys),
        y1: h < DRAG_THRESHOLD ? d.y1 : Math.max(...ys),
      })
    } else if (drag.kind === 'click') {
      void selectAt(local(e))
    }
  }

  const selectAt = async ({ x, y }: { x: number; y: number }) => {
    if (doc.kind !== 'ms' || !transform || !image) return
    const pixel = pixelAt(image.full, transform.rt1(x - plot.left), transform.rt2(y - plot.top))
    if (!pixel) return
    try {
      await api().select(docId, {
        rt2_index: pixel.row,
        mod_index: pixel.col,
        snap: true,
        n_average: 3,
      })
    } catch (err) {
      notifyError('Select point', err)
    }
  }

  // Esc cancels a zoom box
  useEffect(() => {
    if (!drag || drag.kind !== 'zoom') return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrag(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [drag])

  const cursorValue = useView((s) => (s.cursor?.docId === docId ? s.cursor : null))
  const scale = {
    vmin: contrast.vmin,
    vmax: contrast.vmax,
    scaling: display.scaling,
    k: display.k,
  }
  const cursorClass =
    drag?.kind === 'pan'
      ? 'cursor-grabbing'
      : tool === 'pan'
        ? 'cursor-grab'
        : tool === 'zoom'
          ? 'cursor-zoom-in'
          : 'cursor-crosshair'

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full touch-none overflow-hidden select-none"
      data-testid="chromatogram"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={() => setDrag(null)}
      onPointerLeave={() => {
        setHover(null)
        setHoverPeak(null)
        if (useView.getState().cursor?.docId === docId) setCursor(null)
      }}
      onDoubleClick={(e) => {
        const { x, y } = local(e)
        if (inPlot(x, y)) resetZoom(docId)
      }}
    >
      <div
        className="absolute bg-plot-bg"
        style={{ left: plot.left, top: plot.top, width: plot.width, height: plot.height }}
      />
      <canvas
        ref={canvasRef}
        className={`absolute ${cursorClass}`}
        data-testid="chromatogram-canvas"
        style={{ left: plot.left, top: plot.top, width: plot.width, height: plot.height }}
      />
      {size.width > 0 && domain && transform && (
        <svg
          className="pointer-events-none absolute inset-0 overflow-visible"
          width={size.width}
          height={size.height}
        >
          <defs>
            <clipPath id={`plot-clip-${docId}`}>
              <rect x={plot.left} y={plot.top} width={plot.width} height={plot.height} />
            </clipPath>
          </defs>
          <g clipPath={`url(#plot-clip-${docId})`}>
            <Overlays
              plot={plot}
              transform={transform}
              image={image ?? null}
              outlines={display.showMasks ? overlay.outlines : []}
              outlineGeometry={overlay.geometry}
              peaks={display.showPeaks ? peakMarkers : []}
              hoverPeak={hoverPeak}
              selection={selection ?? null}
            />
            {hover && tool === 'select' && !drag && (
              <g>
                {[0, 1].map((halo) => (
                  <g
                    key={halo}
                    className={halo ? 'stroke-plot-crosshair' : 'stroke-plot-halo'}
                    strokeWidth={halo ? 1 : 3}
                  >
                    <line
                      x1={Math.round(hover.x) + 0.5}
                      x2={Math.round(hover.x) + 0.5}
                      y1={plot.top}
                      y2={plot.top + plot.height}
                    />
                    <line
                      x1={plot.left}
                      x2={plot.left + plot.width}
                      y1={Math.round(hover.y) + 0.5}
                      y2={Math.round(hover.y) + 0.5}
                    />
                  </g>
                ))}
              </g>
            )}
            {drag?.kind === 'zoom' && (
              <rect
                x={Math.min(drag.x0, drag.x)}
                y={Math.min(drag.y0, drag.y)}
                width={Math.abs(drag.x - drag.x0)}
                height={Math.abs(drag.y - drag.y0)}
                className="fill-plot-zoom/15 stroke-plot-zoom"
                strokeDasharray="4 3"
              />
            )}
          </g>
          <Axes plot={plot} domain={domain} />
          <ColorBar
            x={plot.left + plot.width + COLORBAR.gap}
            top={plot.top}
            height={plot.height}
            width={COLORBAR.width}
            colormap={display.colormap}
            reverse={display.reverse}
            scale={scale}
          />
        </svg>
      )}
      {hover && cursorValue && !drag && (
        <CursorTooltip
          x={hover.x}
          y={hover.y}
          bounds={size}
          lines={
            hoverPeak
              ? peakLines(hoverPeak)
              : [`${formatValue(cursorValue.value ?? NaN, 4)}${cursorValue.pooled ? ' (max)' : ''}`]
          }
        />
      )}
      {(glError || imageError || (!image && loading)) && (
        <div className="absolute inset-0 flex items-center justify-center p-6 text-sm text-muted-foreground">
          {glError ?? imageError ?? 'Loading image…'}
        </div>
      )}
    </div>
  )
}

function peakLines(p: PeakMarker): string[] {
  const lines = [p.name ? `#${p.id} ${p.name}` : `Peak #${p.id}`]
  if (p.match != null) lines.push(`Match ${Math.round(p.match)}`)
  lines.push(`${p.rt1Min.toFixed(2)} min · ${p.rt2S.toFixed(2)} s`)
  return lines
}

function CursorTooltip({
  x,
  y,
  bounds,
  lines,
}: {
  x: number
  y: number
  bounds: { width: number; height: number }
  lines: string[]
}) {
  const left = x + 14 + 160 > bounds.width ? x - 14 : x + 14
  const top = y + 14 + 20 * lines.length > bounds.height ? y - 14 : y + 14
  return (
    <div
      className="pointer-events-none absolute z-10 rounded-md border bg-popover/95 px-2 py-1 font-mono text-[11px] leading-tight text-popover-foreground tabular shadow-md"
      style={{
        left,
        top,
        transform: `translate(${left < x ? '-100%' : '0'}, ${top < y ? '-100%' : '0'})`,
      }}
      data-testid="cursor-tooltip"
    >
      {lines.map((l, i) => (
        <div key={i} className={i === 0 ? '' : 'text-muted-foreground'}>
          {l}
        </div>
      ))}
    </div>
  )
}
