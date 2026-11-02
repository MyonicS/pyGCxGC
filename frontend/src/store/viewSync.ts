/**
 * View state in projects. The frontend owns display settings and zoom (spec
 * ARCH-1); a copy goes to the backend (`PUT .../view`, debounced) so that a
 * saved project, also one saved from Python, holds the current view. A
 * document that arrives with a view (opened from a project) starts with it.
 */

import type { DocumentInfo, Settings } from '@/api/types'
import { isColormap } from '@/lib/colormaps'
import type { Domain } from '@/lib/geometry'
import { SCALINGS, type Scaling } from '@/lib/scaling'
import { debounce } from '@/lib/utils'

import { useSession } from './session'
import { defaultDisplay, INTERPOLATIONS, useView, type Display, type Interpolation } from './view'

/** What is stored per document. */
export interface SavedView {
  display?: Partial<Display>
  domain?: Domain | null
}

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** The display of a saved view, checked field by field over the defaults. */
export function restoreDisplay(saved: unknown, settings: Settings): Display {
  const d = defaultDisplay(settings)
  if (typeof saved !== 'object' || saved === null) return d
  const s = saved as Record<string, unknown>
  if (typeof s.colormap === 'string' && isColormap(s.colormap)) d.colormap = s.colormap
  if (typeof s.reverse === 'boolean') d.reverse = s.reverse
  if ((SCALINGS as readonly unknown[]).includes(s.scaling)) d.scaling = s.scaling as Scaling
  if (num(s.k) && s.k > 0) d.k = s.k
  if ((INTERPOLATIONS as readonly unknown[]).includes(s.interpolation))
    d.interpolation = s.interpolation as Interpolation
  if (s.contrast === 'manual' && num(s.vmin) && num(s.vmax) && s.vmax > s.vmin) {
    d.contrast = 'manual'
    d.vmin = s.vmin
    d.vmax = s.vmax
  }
  if (num(s.maskOpacity)) d.maskOpacity = Math.min(Math.max(s.maskOpacity, 0), 1)
  if (typeof s.showMasks === 'boolean') d.showMasks = s.showMasks
  if (typeof s.showPeaks === 'boolean') d.showPeaks = s.showPeaks
  if (typeof s.colorbarLabel === 'string') d.colorbarLabel = s.colorbarLabel
  if (typeof s.showColorbarLabel === 'boolean') d.showColorbarLabel = s.showColorbarLabel
  return d
}

export function restoreDomain(saved: unknown): Domain | undefined {
  if (typeof saved !== 'object' || saved === null) return undefined
  const d = saved as Record<string, unknown>
  if (num(d.x0) && num(d.x1) && num(d.y0) && num(d.y1) && d.x1 > d.x0 && d.y1 > d.y0)
    return { x0: d.x0, x1: d.x1, y0: d.y0, y1: d.y1 }
  return undefined
}

/** Apply the saved view of documents seen for the first time. */
function restoreViews(documents: Record<string, DocumentInfo>, seen: Set<string>) {
  const settings = useSession.getState().settings
  for (const doc of Object.values(documents)) {
    if (seen.has(doc.id)) continue
    seen.add(doc.id)
    const view = doc.view as SavedView | null | undefined
    if (!view) continue
    useView.setState((s) => ({
      display: { ...s.display, [doc.id]: restoreDisplay(view.display, settings) },
      domains: { ...s.domains, [doc.id]: restoreDomain(view.domain) },
    }))
  }
}

const pending = new Set<string>()

/** Send the views changed since the last time (before saving a project). */
export async function flushViews(): Promise<void> {
  const { api, documents } = useSession.getState()
  const { display, domains } = useView.getState()
  const ids = [...pending]
  pending.clear()
  await Promise.all(
    ids
      .filter((id) => id in documents && display[id])
      .map((id) => {
        const view: SavedView = { display: display[id], domain: domains[id] ?? null }
        return api?.setView(id, view as Record<string, unknown>).catch(() => undefined)
      }),
  )
}

/** Start restoring and syncing views; returns a function that stops it. */
export function startViewSync(delay = 600): () => void {
  const seen = new Set<string>()
  restoreViews(useSession.getState().documents, seen)
  const stopSession = useSession.subscribe((state, previous) => {
    if (state.documents !== previous.documents) restoreViews(state.documents, seen)
  })
  const flush = debounce(() => void flushViews(), delay)
  const stopView = useView.subscribe((state, previous) => {
    if (state.display === previous.display && state.domains === previous.domains) return
    for (const id of Object.keys(state.display)) {
      if (state.display[id] !== previous.display[id] || state.domains[id] !== previous.domains[id])
        pending.add(id)
    }
    if (pending.size) flush()
  })
  return () => {
    stopSession()
    stopView()
    flush.cancel()
  }
}
