/**
 * The 2D chromatogram view (spec §5): WebGL image, axes, colour bar,
 * overlays, crosshair, the select / pan / zoom-box interactions and the mask
 * drawing tools (spec MASK-4, MASK-6).
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import type { DocumentInfo, Shape } from '@/api/types'
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
import { backgroundOf, figureName, saveFigure, SKIP_ATTRIBUTE } from '@/lib/figure'
import { formatValue } from '@/lib/ticks'
import { debounce } from '@/lib/utils'
import { loadDetail, loadImage, useImages } from '@/store/images'
import { drawShape, replaceShape, useActiveMaskId } from '@/store/masks'
import { selectPeak, selectPoint } from '@/store/ms'
import { useSession } from '@/store/session'
import {
  defaultDisplay,
  effectiveContrast,
  ensureDisplay,
  isDrawTool,
  resetZoom,
  selectShape,
  setCursor,
  setDomain,
  updateDisplay,
  useView,
  type Cursor,
} from '@/store/view'

import { Axes, type PlotRect } from './Axes'
import { ColorBar, valueLabelText } from './ColorBar'
import { registerChromFigure } from './figure'
import { autoVmax, scanAt, valueAt, visibleRegion } from './imageMath'
import { Overlays, nearestPeak, type PeakMarker } from './Overlays'
import { ChromRenderer, WebGLUnavailableError, type DrawParams } from './renderer'
import { ShapeLayer, type Draft } from './ShapeLayer'
import {
  contains,
  hitHandle,
  isVector,
  moveHandle,
  rectShape,
  screenDistance,
  screenSize,
  translate,
  vertices,
  type Point,
} from './shapes'
import { useMaskOverlay } from './useMaskOverlay'

const MARGIN = { left: 62, right: 90, top: 12, bottom: 44 }
/** Extra right margin for the colour bar label. */
const LABEL_MARGIN = 16
const COLORBAR = { gap: 14, width: 14 }
/** Smallest visible span, in pixels of the full-resolution grid. */
const MIN_PIXELS = 1.5
/** Pointer travel (px) that turns a click into a drag. */
const DRAG_THRESHOLD = 4
/** A click this close (px) to the first polygon vertex closes the polygon. */
const CLOSE_RADIUS = 8
/** Lasso points closer than this (px) are skipped. */
const LASSO_STEP = 2
const NEUTRAL = '#9ca3af'

type Drag =
  | { kind: 'pan'; pointerId: number; x: number; y: number; domain: Domain }
  | { kind: 'zoom'; pointerId: number; x0: number; y0: number; x: number; y: number }
  | { kind: 'click'; pointerId: number; x: number; y: number; domain: Domain }
  | { kind: 'rect'; pointerId: number; points: [Point, Point]; subtract: boolean }
  | { kind: 'lasso'; pointerId: number; points: Point[]; subtract: boolean }
  /** Dragging a handle (index >= 0) or the whole selected shape (index -1). */
  | {
      kind: 'edit'
      pointerId: number
      index: number
      start: Point
      original: Shape
      shape: Shape
      moved: boolean
    }

export function ChromView({ doc }: { doc: DocumentInfo }) {
  const docId = doc.id
  const containerRef = useRef<HTMLDivElement>(null)
  const rendererRef = useRef<ChromRenderer | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const plotBgRef = useRef<HTMLDivElement>(null)
  /** What the last frame was drawn with (figure export). */
  const frameRef = useRef<{ params: DrawParams; plot: PlotRect; size: typeof size } | null>(null)
  const [glError, setGlError] = useState<string | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [drag, setDrag] = useState<Drag | null>(null)
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null)
  const [hoverPeak, setHoverPeak] = useState<PeakMarker | null>(null)
  const [hoverHandle, setHoverHandle] = useState(false)
  const [polygonState, setPolygonState] = useState<{
    points: Point[]
    subtract: boolean
    tool: string
    docId: string
  } | null>(null)
  const [pendingState, setPendingState] = useState<
    { shape: Shape; subtract: boolean; overlay: unknown }[]
  >([])
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
  const subtractMode = useView((s) => s.subtract)
  const storedDomain = useView((s) => s.domains[docId])
  const shiftPreview = useView((s) => s.shiftPreview[docId])
  const colorbarLabel = display.showColorbarLabel
    ? display.colorbarLabel.trim() || valueLabelText(doc.value_label)
    : ''
  const marginRight = MARGIN.right + (colorbarLabel ? LABEL_MARGIN : 0)
  const selected = useView((s) => (s.selectedShape?.docId === docId ? s.selectedShape : null))
  const overlay = useMaskOverlay(docId, masks, doc.revision)
  const activeMaskId = useActiveMaskId(docId)
  const activeMask = masks?.masks.find((m) => m.id === activeMaskId)
  const drawColor = activeMask?.color ?? NEUTRAL

  // the selected vector shape (an imported raster has no handles)
  const selectedOp = useMemo(() => {
    if (!selected) return null
    const mask = masks?.masks.find((m) => m.id === selected.maskId)
    const op = mask?.operations.find((o) => o.id === selected.opId)
    const shape = op?.shape as Shape | undefined
    return mask && shape && isVector(shape) ? { mask, shape } : null
  }, [selected, masks])

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

  // a drawn shape stays outlined until Python's raster of it arrives (the
  // overlay changes); switching tools or documents abandons a polygon. Stale
  // shapes are dropped, or they would come back when the overlay returns to
  // an earlier value (empty again after undoing the first shape)
  const [pendingOverlay, setPendingOverlay] = useState(overlay)
  if (pendingOverlay !== overlay) {
    setPendingOverlay(overlay)
    setPendingState([])
  }
  const pending = pendingState.filter((p) => p.overlay === overlay)
  const polygon =
    polygonState && polygonState.tool === tool && polygonState.docId === docId ? polygonState : null
  const setPolygon = (points: Point[] | null, subtract = polygon?.subtract ?? false) =>
    setPolygonState(points ? { points, subtract, tool, docId } : null)
  const addPending = (shape: Shape, subtract: boolean) =>
    setPendingState((p) => [
      ...p.filter((q) => q.overlay === overlay),
      { shape, subtract, overlay },
    ])

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
      width: Math.max(1, size.width - MARGIN.left - marginRight),
      height: Math.max(1, size.height - MARGIN.top - MARGIN.bottom),
    }),
    [size, marginRight],
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
    const params: DrawParams = {
      domain,
      vmin: contrast.vmin,
      vmax: contrast.vmax,
      scaling: display.scaling,
      k: display.k,
      interpolation: display.interpolation,
      lut,
      shiftRows,
      overlayOpacity: display.showMasks && overlay.layer ? display.maskOpacity : 0,
    }
    renderer.draw(params)
    frameRef.current = { params, plot, size }
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
    size,
  ])

  // figure export (spec VIEW-12): the image at 2× under the SVG layers
  const figureBase = figureName(
    doc.name,
    doc.kind === 'ms' ? (doc.signal === 'eic' ? `EIC-${doc.mz?.join('-')}` : 'TIC') : null,
  )
  useEffect(
    () =>
      registerChromFigure(docId, async (format, theme) => {
        const renderer = rendererRef.current
        const svg = svgRef.current
        const frame = frameRef.current
        if (!renderer || !svg || !frame) throw new Error('The chromatogram is not shown yet')
        const { params, plot: p, size: s } = frame
        // the image does not depend on the theme (spec THEME-3)
        const png = renderer.snapshot(params, p.width, p.height, 2)
        await saveFigure(svg, figureBase, format, theme, () => {
          const plotBg = plotBgRef.current
            ? getComputedStyle(plotBgRef.current).backgroundColor
            : ''
          return {
            width: s.width,
            height: s.height,
            background: backgroundOf(containerRef.current),
            prepare: (clone) => {
              const ns = 'http://www.w3.org/2000/svg'
              const after = clone.querySelector('defs')?.nextSibling ?? clone.firstChild
              const bg = document.createElementNS(ns, 'rect')
              const img = document.createElementNS(ns, 'image')
              for (const el of [bg, img]) {
                el.setAttribute('x', String(p.left))
                el.setAttribute('y', String(p.top))
                el.setAttribute('width', String(p.width))
                el.setAttribute('height', String(p.height))
              }
              if (plotBg) bg.setAttribute('fill', plotBg)
              img.setAttribute('href', png)
              img.setAttribute('preserveAspectRatio', 'none')
              clone.insertBefore(bg, after)
              clone.insertBefore(img, after)
            },
          }
        })
      }),
    [docId, figureBase],
  )

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
  /** Retention times of a container position. */
  const rtAt = (x: number, y: number): Point =>
    transform ? [transform.rt1(x - plot.left), transform.rt2(y - plot.top)] : [0, 0]

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
    const [rt1, rt2] = rtAt(x, y)
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

  /** Handle of the selected shape under a container position (-1: none). */
  const handleAt = (x: number, y: number) =>
    selectedOp && transform
      ? hitHandle(vertices(selectedOp.shape), transform, x - plot.left, y - plot.top)
      : -1

  const commitPolygon = (points: Point[], subtract: boolean) => {
    setPolygon(null)
    if (points.length < 3 || !transform) return
    const [w, h] = screenSize(transform, points)
    if (w < 3 && h < 3) return
    const shape: Shape = { type: 'polygon', vertices: points }
    addPending(shape, subtract)
    void drawShape(docId, shape, subtract ? 'subtract' : 'add')
  }

  const onPointerDown = (e: React.PointerEvent) => {
    if (!domain || !transform) return
    const { x, y } = local(e)
    if (!inPlot(x, y)) return
    const middle = e.button === 1
    if (e.button !== 0 && !middle) return
    e.preventDefault()
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    const id = e.pointerId
    const p = rtAt(x, y)
    const subtract = subtractMode || e.altKey
    if (middle || tool === 'pan') {
      setDrag({ kind: 'pan', pointerId: id, x, y, domain })
      return
    }
    // editing the selected shape takes precedence over the tools
    const handle = handleAt(x, y)
    if (selectedOp && (handle >= 0 || (tool === 'select' && contains(selectedOp.shape, p)))) {
      const shape = selectedOp.shape
      setDrag({
        kind: 'edit',
        pointerId: id,
        index: handle,
        start: p,
        original: shape,
        shape,
        moved: false,
      })
      return
    }
    if (tool === 'zoom') setDrag({ kind: 'zoom', pointerId: id, x0: x, y0: y, x, y })
    else if (tool === 'rect') setDrag({ kind: 'rect', pointerId: id, points: [p, p], subtract })
    else if (tool === 'lasso') setDrag({ kind: 'lasso', pointerId: id, points: [p], subtract })
    else if (tool === 'polygon') {
      if (!polygon) {
        setPolygon([p], subtract)
      } else if (
        polygon.points.length >= 3 &&
        screenDistance(transform, polygon.points[0], p) <= CLOSE_RADIUS
      ) {
        commitPolygon(polygon.points, polygon.subtract)
      } else {
        const last = polygon.points.at(-1)!
        // the second click of a double click lands on the same spot
        if (screenDistance(transform, last, p) >= 3) {
          setPolygon([...polygon.points, p])
        }
      }
    } else setDrag({ kind: 'click', pointerId: id, x, y, domain })
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
      setHoverHandle(handleAt(x, y) >= 0)
    }
    if (!drag || drag.pointerId !== e.pointerId) return
    const p = rtAt(
      Math.min(Math.max(x, plot.left), plot.left + plot.width),
      Math.min(Math.max(y, plot.top), plot.top + plot.height),
    )
    switch (drag.kind) {
      case 'zoom': {
        const cx = Math.min(Math.max(x, plot.left), plot.left + plot.width)
        const cy = Math.min(Math.max(y, plot.top), plot.top + plot.height)
        setDrag({ ...drag, x: cx, y: cy })
        return
      }
      case 'rect':
        setDrag({ ...drag, points: [drag.points[0], p] })
        return
      case 'lasso': {
        const last = drag.points.at(-1)!
        if (transform && screenDistance(transform, last, p) >= LASSO_STEP) {
          setDrag({ ...drag, points: [...drag.points, p] })
        }
        return
      }
      case 'edit': {
        const shape =
          drag.index >= 0
            ? moveHandle(drag.original, drag.index, p)
            : translate(drag.original, p[0] - drag.start[0], p[1] - drag.start[1])
        setDrag({ ...drag, shape, moved: true })
        return
      }
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
    if (!drag || drag.pointerId !== e.pointerId || !transform) return
    setDrag(null)
    if (drag.kind === 'zoom') {
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
    } else if (drag.kind === 'rect' || drag.kind === 'lasso') {
      const [w, h] = screenSize(transform, drag.points)
      if (w < 3 || h < 3 || (drag.kind === 'lasso' && drag.points.length < 3)) return
      const shape: Shape =
        drag.kind === 'rect'
          ? rectShape(drag.points[0], drag.points[1])
          : { type: 'lasso', vertices: drag.points }
      addPending(shape, drag.subtract)
      void drawShape(docId, shape, drag.subtract ? 'subtract' : 'add')
    } else if (drag.kind === 'edit') {
      if (drag.moved && selected) {
        addPending(drag.shape, false)
        void replaceShape(docId, selected.maskId, selected.opId, drag.shape)
      }
    } else if (drag.kind === 'click') {
      clickAt(local(e))
    }
  }

  /** A click: select the topmost shape of the active mask under it, else a point (MS). */
  const clickAt = ({ x, y }: { x: number; y: number }) => {
    const p = rtAt(x, y)
    if (activeMask && display.showMasks) {
      const hit = [...activeMask.operations]
        .reverse()
        .find((op) => isVector(op.shape as Shape) && contains(op.shape as Shape, p))
      if (hit) {
        selectShape({ docId, maskId: activeMask.id, opId: hit.id })
        return
      }
    }
    if (selected) selectShape(null)
    if (doc.kind !== 'ms' || !image) return
    // a click on a peak marker selects the peak (its apex, for "Assign hit")
    const row = hoverPeak && peaks?.rows.find((r) => Number(r.peak_id) === hoverPeak.id)
    if (row) {
      void selectPeak(docId, { peak_id: Number(row.peak_id), scan: Number(row.scan) })
      return
    }
    const pixel = pixelAt(image.full, p[0], p[1])
    if (pixel) void selectPoint(docId, pixel.row, pixel.col)
  }

  // keys while drawing: Enter closes a polygon, Backspace removes its last
  // vertex, Esc cancels the gesture or the selection
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
      if (e.key === 'Escape') {
        if (drag) setDrag(null)
        else if (polygon) setPolygon(null)
        else if (selected) selectShape(null)
        else return
      } else if (e.key === 'Enter' && polygon) {
        commitPolygon(polygon.points, polygon.subtract)
      } else if (e.key === 'Backspace' && polygon) {
        setPolygon(polygon.points.length > 1 ? polygon.points.slice(0, -1) : null)
      } else return
      e.preventDefault()
      e.stopPropagation()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  })

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
      : drag?.kind === 'edit' || hoverHandle
        ? 'cursor-move'
        : tool === 'pan'
          ? 'cursor-grab'
          : tool === 'zoom'
            ? 'cursor-zoom-in'
            : 'cursor-crosshair'

  let draft: Draft | null = null
  if (drag?.kind === 'rect' || drag?.kind === 'lasso') {
    draft = { points: drag.points, kind: drag.kind, subtract: drag.subtract }
  } else if (polygon) {
    draft = { points: polygon.points, kind: 'polygon', subtract: polygon.subtract }
  }
  const editedShape = drag?.kind === 'edit' ? drag.shape : (selectedOp?.shape ?? null)
  const showCrosshair = hover && !drag && (tool === 'select' || isDrawTool(tool))

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
        if (!inPlot(x, y)) return
        if (tool === 'polygon') {
          if (polygon) commitPolygon(polygon.points, polygon.subtract)
        } else if (!isDrawTool(tool)) resetZoom(docId)
      }}
    >
      <div
        ref={plotBgRef}
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
          ref={svgRef}
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
            <g {...{ [SKIP_ATTRIBUTE]: '' }}>
              <ShapeLayer
                plot={plot}
                transform={transform}
                color={drawColor}
                draft={draft}
                cursor={polygon && hover ? rtAt(hover.x, hover.y) : null}
                pending={pending}
                selected={
                  editedShape && selectedOp
                    ? { shape: editedShape, color: selectedOp.mask.color }
                    : null
                }
              />
              {showCrosshair && (
                <g>
                  {[0, 1].map((halo) => (
                    <g
                      key={halo}
                      className={halo ? 'stroke-plot-crosshair' : 'stroke-plot-halo'}
                      strokeWidth={halo ? 1 : 3}
                      opacity={isDrawTool(tool) ? 0.6 : 1}
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
            label={colorbarLabel}
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
