/**
 * View state (spec ARCH-1: the only state the frontend owns): display
 * settings and zoom per document, the active tool, contrast lock, cursor
 * readout and the phase-shift preview.
 */

import { create } from 'zustand'

import type { Settings } from '@/api/types'
import { isColormap, type ColormapName } from '@/lib/colormaps'
import type { Domain } from '@/lib/geometry'
import { DEFAULT_LOG_RANGE, SCALINGS, type Scaling } from '@/lib/scaling'

export const INTERPOLATIONS = ['nearest', 'bilinear', 'bicubic'] as const
export type Interpolation = (typeof INTERPOLATIONS)[number]
export const INTERPOLATION_LABELS: Record<Interpolation, string> = {
  nearest: 'None',
  bilinear: 'Bilinear',
  bicubic: 'Bicubic',
}

export type Tool = 'select' | 'pan' | 'zoom' | 'rect' | 'lasso' | 'polygon'

/** Tools that draw mask shapes (spec MASK-4). */
export const DRAW_TOOLS: readonly Tool[] = ['rect', 'lasso', 'polygon']
export const isDrawTool = (t: Tool) => DRAW_TOOLS.includes(t)

/** A shape (operation) of a mask, selected for editing (spec MASK-6). */
export interface SelectedShape {
  docId: string
  maskId: string
  opId: string
}

export interface Display {
  colormap: ColormapName
  reverse: boolean
  scaling: Scaling
  /** Dynamic range `k` of the log scaling. */
  k: number
  interpolation: Interpolation
  /** `null` until the first image arrives (then set automatically). */
  vmin: number | null
  vmax: number | null
  /** `auto` contrast follows new images; `manual` keeps typed values. */
  contrast: 'auto' | 'manual'
  maskOpacity: number
  showMasks: boolean
  showPeaks: boolean
}

export interface Cursor {
  docId: string
  rt1: number
  rt2: number
  /** Full-resolution pixel. */
  row: number
  col: number
  value: number | null
  /** The value comes from a max-pooled overview. */
  pooled: boolean
  scan: number | null
}

export interface ViewState {
  display: Record<string, Display>
  domains: Record<string, Domain | undefined>
  tool: Tool
  /** Contrast shared by all documents while locked (spec VIEW-6). */
  lock: { vmin: number; vmax: number } | null
  cursor: Cursor | null
  /** Phase shift being dragged in the Processing panel (spec FID-5). */
  shiftPreview: Record<string, number | undefined>
  /** Drawing subtracts from the active mask (or hold Alt). */
  subtract: boolean
  /** The mask new shapes go into, per document (spec MASK-3). */
  activeMask: Record<string, string | undefined>
  selectedShape: SelectedShape | null
}

export const useView = create<ViewState>(() => ({
  display: {},
  domains: {},
  tool: 'select',
  lock: null,
  cursor: null,
  shiftPreview: {},
  subtract: false,
  activeMask: {},
  selectedShape: null,
}))

const set = useView.setState
const get = useView.getState

/** Display defaults from the settings (spec VIEW-8). */
export function defaultDisplay(settings: Settings): Display {
  const d = settings.display ?? {}
  return {
    colormap: isColormap(d.colormap) ? d.colormap : 'viridis',
    reverse: Boolean(d.reverse),
    scaling: (SCALINGS as readonly string[]).includes(d.scaling ?? '')
      ? (d.scaling as Scaling)
      : 'sqrt',
    k: typeof d.log_range === 'number' && d.log_range > 0 ? d.log_range : DEFAULT_LOG_RANGE,
    interpolation: (INTERPOLATIONS as readonly string[]).includes(d.interpolation ?? '')
      ? (d.interpolation as Interpolation)
      : 'nearest',
    vmin: null,
    vmax: null,
    contrast: 'auto',
    maskOpacity: 0.35,
    showMasks: true,
    showPeaks: true,
  }
}

export function ensureDisplay(docId: string, settings: Settings): Display {
  const existing = get().display[docId]
  if (existing) return existing
  const display = defaultDisplay(settings)
  set((s) => ({ display: { ...s.display, [docId]: display } }))
  return display
}

export function updateDisplay(docId: string, changes: Partial<Display>) {
  set((s) => {
    const current = s.display[docId]
    if (!current) return {}
    return { display: { ...s.display, [docId]: { ...current, ...changes } } }
  })
}

/** Set the contrast by hand (or, while locked, the shared contrast). */
export function setContrast(docId: string, vmin: number, vmax: number) {
  if (get().lock) set({ lock: { vmin, vmax } })
  updateDisplay(docId, { vmin, vmax, contrast: 'manual' })
}

export function setLock(docId: string, locked: boolean) {
  const d = get().display[docId]
  if (locked && d?.vmin != null && d.vmax != null) set({ lock: { vmin: d.vmin, vmax: d.vmax } })
  else if (!locked) {
    const lock = get().lock
    // documents keep the locked contrast instead of jumping back
    if (lock) updateDisplay(docId, { ...lock, contrast: 'manual' })
    set({ lock: null })
  }
}

/** Contrast in force for a document. */
export function effectiveContrast(d: Display, lock: ViewState['lock']) {
  return lock ?? { vmin: d.vmin ?? 0, vmax: d.vmax ?? 1 }
}

export function setDomain(docId: string, domain: Domain | undefined) {
  set((s) => ({ domains: { ...s.domains, [docId]: domain } }))
}

export function setTool(tool: Tool) {
  set({ tool })
}

export function setSubtract(subtract: boolean) {
  set({ subtract })
}

export function setActiveMask(docId: string, maskId: string | undefined) {
  set((s) => ({
    activeMask: { ...s.activeMask, [docId]: maskId },
    // a selected shape belongs to the active mask
    selectedShape: s.selectedShape?.maskId === maskId ? s.selectedShape : null,
  }))
}

export function selectShape(shape: SelectedShape | null) {
  set((s) => ({
    selectedShape: shape,
    activeMask: shape ? { ...s.activeMask, [shape.docId]: shape.maskId } : s.activeMask,
  }))
}

/**
 * The active mask of a document: the chosen one while it exists, else the
 * last mask (new masks are appended), else none.
 */
export function resolveActiveMask(
  chosen: string | undefined,
  maskIds: readonly string[],
): string | undefined {
  if (chosen && maskIds.includes(chosen)) return chosen
  return maskIds.at(-1)
}

export function setCursor(cursor: Cursor | null) {
  set({ cursor })
}

export function setShiftPreview(docId: string, shift: number | undefined) {
  set((s) => ({ shiftPreview: { ...s.shiftPreview, [docId]: shift } }))
}

export function forgetDocument(docId: string) {
  set((s) => {
    const display = { ...s.display }
    const domains = { ...s.domains }
    const shiftPreview = { ...s.shiftPreview }
    delete display[docId]
    delete domains[docId]
    delete shiftPreview[docId]
    const activeMask = { ...s.activeMask }
    delete activeMask[docId]
    return {
      display,
      domains,
      shiftPreview,
      activeMask,
      cursor: s.cursor?.docId === docId ? null : s.cursor,
      selectedShape: s.selectedShape?.docId === docId ? null : s.selectedShape,
    }
  })
}

/** Show the whole image (an undefined domain means the full extent). */
export function resetZoom(docId: string) {
  setDomain(docId, undefined)
}
