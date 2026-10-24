/**
 * GCxGC-MS commands and view state (spec §7): point and region selection with
 * library search, the EIC, peaks, libraries, spectra and structures.
 *
 * The selection, hits, peak table and libraries live in Python (ARCH-1) and
 * reach the store through events; this module only keeps view state: the
 * selected hit and peak, and the spectrum display toggles.
 */

import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { create } from 'zustand'

import type {
  DocumentInfo,
  HitInfo,
  PeakEdit,
  PeakRow,
  SelectionInfo,
  StructureInfo,
} from '@/api/types'
import { notifyError, notifyInfo } from '@/lib/notify'
import { sticksOf, type Sticks } from '@/lib/spectrum'
import { basename } from '@/lib/utils'

import { saveFile } from './masks'
import { api, updateSettings, useSession, waitForJob } from './session'

// ------------------------------------------------------------------ view state
interface MsViewState {
  /** Spectrum panel: background corrected or raw (spec MS-5). */
  corrected: boolean
  /** Relative (base peak 999) or absolute intensities. */
  relative: boolean
  /** Selected hit per document, for the hits it was chosen from. */
  hit: Record<string, { key: string; rank: number } | undefined>
  /** Selected peak per document, while its point is the selection. */
  peak: Record<string, { peakId: number; selectionId: number } | undefined>
}

export const useMsView = create<MsViewState>(() => ({
  corrected: true,
  relative: true,
  hit: {},
  peak: {},
}))

const setView = useMsView.setState

export function setSpectrumDisplay(changes: Partial<Pick<MsViewState, 'corrected' | 'relative'>>) {
  setView(changes)
}

/** Point settings (spec MS-4), kept in the user settings. */
export function pointSettings() {
  const ms = useSession.getState().settings.ms ?? {}
  return { snap: ms.snap ?? true, n_average: ms.n_average ?? 3 }
}

export function usePointSettings() {
  const snap = useSession((s) => s.settings.ms?.snap ?? true)
  const nAverage = useSession((s) => s.settings.ms?.n_average ?? 3)
  return { snap, nAverage }
}

export function setPointSettings(changes: { snap?: boolean; n_average?: number }) {
  void updateSettings({ ms: changes })
}

/** Identity of a hit list: the selected hit resets when the hits change. */
export function hitsKey(sel: SelectionInfo | null | undefined): string {
  if (!sel?.hits) return ''
  return `${sel.id}:${sel.hits.map((h) => `${h.library_id}/${h.index}`).join(',')}`
}

/** The selected hit (the top hit by default, spec MS-6). */
export function useSelectedHit(docId: string | undefined): HitInfo | undefined {
  const sel = useSession((s) => (docId ? s.selections[docId] : undefined))
  const chosen = useMsView((s) => (docId ? s.hit[docId] : undefined))
  const hits = sel?.hits
  if (!hits?.length) return undefined
  const rank = chosen && chosen.key === hitsKey(sel) ? chosen.rank : 1
  return hits.find((h) => h.rank === rank) ?? hits[0]
}

export function selectHit(docId: string, rank: number) {
  const sel = useSession.getState().selections[docId]
  setView((s) => ({ hit: { ...s.hit, [docId]: { key: hitsKey(sel), rank } } }))
}

/** The selected peak while the selection is its apex. */
export function useSelectedPeakId(docId: string | undefined): number | undefined {
  const selId = useSession((s) => (docId ? s.selections[docId]?.id : undefined))
  const peak = useMsView((s) => (docId ? s.peak[docId] : undefined))
  return peak && peak.selectionId === selId ? peak.peakId : undefined
}

// ------------------------------------------------------------------ selection
async function searchSelection(docId: string) {
  try {
    await api().search(docId)
  } catch (err) {
    notifyError('Library search', err)
  }
}

/** Click to search (spec MS-4): select a pixel, show its spectrum, then search it. */
export async function selectPoint(docId: string, rt2Index: number, modIndex: number) {
  try {
    await api().select(docId, { rt2_index: rt2Index, mod_index: modIndex, ...pointSettings() })
  } catch (err) {
    notifyError('Select point', err)
    return
  }
  await searchSelection(docId)
}

/** Select a peak's apex scan (no snapping) and search it. */
export async function selectPeak(docId: string, peak: Pick<PeakRow, 'peak_id' | 'scan'>) {
  try {
    const sel = await api().select(docId, {
      scan: peak.scan,
      snap: false,
      n_average: pointSettings().n_average,
    })
    setView((s) => ({
      peak: { ...s.peak, [docId]: { peakId: peak.peak_id, selectionId: sel.id } },
    }))
  } catch (err) {
    notifyError('Select peak', err)
    return
  }
  await searchSelection(docId)
}

/** The summed spectrum of a mask (spec MS-11), searched like a point. */
export async function selectRegion(docId: string, maskId: string) {
  try {
    await api().select(docId, { mask_id: maskId })
  } catch (err) {
    notifyError('Region spectrum', err)
    return
  }
  await searchSelection(docId)
}

// ------------------------------------------------------------------ signal
/** Parse an m/z list ("91", "57 71 85", "91, 105"); `null` if invalid. */
export function parseMzList(text: string): number[] | null {
  const parts = text.split(/[\s,;]+/).filter(Boolean)
  const values = parts.map(Number)
  if (values.some((v) => !Number.isInteger(v) || v <= 0)) return null
  return [...new Set(values)].sort((a, b) => a - b)
}

/** TIC, or the EIC of an m/z list (spec MS-3); empty text returns to the TIC. */
export async function setSignal(doc: DocumentInfo, text: string): Promise<boolean> {
  const mz = parseMzList(text)
  if (!mz) {
    notifyInfo('Invalid m/z list', 'Enter whole numbers such as "91" or "57 71 85".')
    return false
  }
  const [lo, hi] = doc.mass_range ?? [0, Infinity]
  const outside = mz.filter((m) => m < lo || m > hi)
  if (outside.length) {
    notifyInfo('m/z outside the data', `The run covers m/z ${lo}–${hi}.`)
    return false
  }
  try {
    const { job } = await api().setSignal(doc.id, mz.length ? 'eic' : 'tic', mz)
    return (await waitForJob(job)).status === 'done'
  } catch (err) {
    notifyError('Signal', err)
    return false
  }
}

// ------------------------------------------------------------------ peaks
export async function detectPeaks(docId: string, snr: number) {
  void updateSettings({ ms: { peak_snr: snr } })
  try {
    const { job } = await api().detectPeaks(docId, {
      snr,
      identify: true,
      n_average: pointSettings().n_average,
    })
    const done = await waitForJob(job)
    if (done.status === 'done') {
      const n = (done.result as { n_peaks?: number } | null)?.n_peaks ?? 0
      toast.success(`${n} peaks`, { description: 'Detected and identified.' })
    }
  } catch (err) {
    notifyError('Detect peaks', err)
  }
}

export async function reidentifyPeaks(docId: string) {
  try {
    const { job } = await api().reidentifyPeaks(docId)
    await waitForJob(job)
  } catch (err) {
    notifyError('Re-identify peaks', err)
  }
}

export async function editPeak(docId: string, edit: PeakEdit, label = 'Edit peak') {
  try {
    await api().editPeaks(docId, [edit])
  } catch (err) {
    notifyError(label, err)
  }
}

export function peakRows(docId: string | undefined): PeakRow[] {
  const table = docId ? useSession.getState().peaks[docId] : null
  return (table?.rows ?? []) as unknown as PeakRow[]
}

// ------------------------------------------------------------------ exports
function stemOf(doc: DocumentInfo) {
  return doc.name.replace(/\.[^.]+$/, '') || 'chromatogram'
}

/** Copy the selected spectrum as text, `m/z<TAB>intensity` per line (spec MS-10). */
export async function copySpectrum(docId: string) {
  try {
    const file = await api().exportFile(docId, { what: 'spectrum', format: 'txt' })
    await navigator.clipboard.writeText(new TextDecoder().decode(file.data))
    toast.success('Spectrum copied')
  } catch (err) {
    notifyError('Copy spectrum', err)
  }
}

export async function downloadSpectrum(docId: string) {
  try {
    saveFile(await api().exportFile(docId, { what: 'spectrum', format: 'msp' }))
  } catch (err) {
    notifyError('Save spectrum', err)
  }
}

export async function downloadPeaks(doc: DocumentInfo) {
  try {
    const file = await api().exportFile(doc.id, { what: 'peaks', format: 'csv' })
    saveFile({ ...file, filename: file.filename || `${stemOf(doc)}_peaks.csv` })
  } catch (err) {
    notifyError('Export peaks', err)
  }
}

/**
 * Use the selected point as the background of spectra (spec BASE-2,
 * `reference`), keeping the image baseline in force.
 */
export async function setAsBackground(doc: DocumentInfo, sel: SelectionInfo) {
  const current = (doc.params as { baseline?: Record<string, unknown> }).baseline ?? {}
  const imageModel =
    current.mode === 'local' || current.mode === 'reference'
      ? current.image_model
      : current.mode === 'none'
        ? { mode: 'modulation_percentile', percentile: 25 }
        : current
  try {
    const { job } = await api().updateDocument(doc.id, {
      baseline: {
        mode: 'reference',
        scan: sel.scan,
        n_scans: sel.n_average,
        image_model: imageModel,
      } as never,
    })
    const done = await waitForJob(job)
    if (done.status === 'done') {
      toast.success('Background set', {
        description: `Spectra subtract scan ${sel.scan} (${sel.n_average} scans).`,
      })
    }
  } catch (err) {
    notifyError('Set as background', err)
  }
}

// ------------------------------------------------------------------ libraries
async function refreshLibraries() {
  const libs = await api().libraries()
  useSession.setState({ libraries: Object.fromEntries(libs.map((l) => [l.id, l])) })
}

/** Add a library (path, or `"mona"` to download MoNA GC-MS) as a job (spec LIB-1). */
export async function addLibrary(path: string) {
  try {
    const { job } = await api().addLibrary(path, true, false)
    const done = await waitForJob(job)
    if (done.status === 'done') {
      const id = (done.result as { library_id?: string } | null)?.library_id
      const lib = id ? useSession.getState().libraries[id] : undefined
      toast.success(`Library ${lib?.name ?? basename(path)} ready`, {
        description: lib?.n_spectra ? `${lib.n_spectra.toLocaleString()} spectra` : undefined,
      })
    }
  } catch (err) {
    notifyError('Add library', err)
  }
}

/** Search a library or not (several can be active, spec LIB-3). */
export async function toggleLibrary(libraryId: string, active: boolean) {
  try {
    await api().setLibraryActive(libraryId, active, false)
    await refreshLibraries()
  } catch (err) {
    notifyError('Library', err)
  }
}

export async function removeLibrary(libraryId: string) {
  try {
    await api().removeLibrary(libraryId)
    await refreshLibraries()
  } catch (err) {
    notifyError('Remove library', err)
  }
}

// ------------------------------------------------------------------ spectra
export interface SelectionSpectrum {
  key: string
  raw: Sticks
  corrected: Sticks
}

/** What changes the spectrum of a selection (re-processing keeps its id). */
export function spectrumKey(sel: SelectionInfo | null | undefined, revision: number) {
  if (!sel) return null
  return [
    sel.id,
    sel.scan,
    sel.n_average,
    sel.baseline,
    sel.snr,
    sel.region?.mask_id,
    revision,
  ].join('|')
}

const useSpectra = create<Record<string, SelectionSpectrum | undefined>>(() => ({}))
const spectrumRequests = new Map<string, Promise<void>>()

/** The spectrum of a document's selection (shared by the Spectrum and Match panels). */
export function useSelectionSpectrum(doc: DocumentInfo | undefined): SelectionSpectrum | null {
  const sel = useSession((s) => (doc ? s.selections[doc.id] : undefined))
  const key = doc ? spectrumKey(sel, doc.revision) : null
  const stored = useSpectra((s) => (doc ? s[doc.id] : undefined))
  useEffect(() => {
    if (!doc || !key || stored?.key === key) return
    const requestKey = `${doc.id}:${key}`
    if (spectrumRequests.has(requestKey)) return
    const request = api()
      .spectrum(doc.id)
      .then(({ data }) => {
        // a newer selection may have arrived meanwhile
        const current = spectrumKey(useSession.getState().selections[doc.id], doc.revision)
        if (current !== key) return
        useSpectra.setState({
          [doc.id]: { key, raw: sticksOf(data, 3, 1), corrected: sticksOf(data, 3, 2) },
        })
      })
      .catch((err: unknown) => {
        if (spectrumKey(useSession.getState().selections[doc.id], doc.revision) === key)
          notifyError('Spectrum', err)
      })
      .finally(() => spectrumRequests.delete(requestKey))
    spectrumRequests.set(requestKey, request)
  }, [doc, key, stored?.key])
  return stored && stored.key === key ? stored : null
}

const librarySpectra = new Map<string, Promise<Sticks>>()

/** A library spectrum (cached). */
export function useLibrarySpectrum(hit: HitInfo | undefined): Sticks | null {
  const id = hit?.library_id ? `${hit.library_id}:${hit.index}` : null
  const [state, setState] = useState<{ id: string; sticks: Sticks } | null>(null)
  useEffect(() => {
    if (!id || !hit?.library_id) return
    let request = librarySpectra.get(id)
    if (!request) {
      request = api()
        .librarySpectrum(hit.library_id, hit.index)
        .then(({ data }) => sticksOf(data, 2, 1))
      librarySpectra.set(id, request)
      request.catch(() => librarySpectra.delete(id))
    }
    let alive = true
    request.then(
      (sticks) => alive && setState({ id, sticks }),
      (err: unknown) => alive && notifyError('Library spectrum', err),
    )
    return () => {
      alive = false
    }
  }, [id, hit?.library_id, hit?.index])
  return state && state.id === id ? state.sticks : null
}

// ------------------------------------------------------------------ structures
export interface StructureState {
  info: StructureInfo | null
  loading: boolean
  error: string | null
}

/**
 * Structure of a hit (spec §8). A pending online lookup is waited for and
 * the structure fetched again; `refresh` re-requests (e.g. after the user
 * allowed online lookups).
 */
export function useStructure(hit: HitInfo | undefined) {
  const id = hit?.library_id ? `${hit.library_id}:${hit.index}` : null
  const [state, setState] = useState<StructureState & { id: string | null }>({
    id: null,
    info: null,
    loading: false,
    error: null,
  })
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    if (!id || !hit?.library_id) return
    let alive = true
    const libraryId = hit.library_id
    const index = hit.index
    const load = async () => {
      let info = await api().structure(libraryId, index)
      if (info.status === 'pending' && info.job) {
        if (alive) setState({ id, info, loading: true, error: null })
        await waitForJob(info.job)
        info = await api().structure(libraryId, index)
      }
      if (alive) setState({ id, info, loading: false, error: null })
    }
    load().catch((err: unknown) => {
      if (alive) setState({ id, info: null, loading: false, error: String(err) })
    })
    return () => {
      alive = false
    }
  }, [id, hit?.library_id, hit?.index, nonce])
  const current = state.id === id ? state : { info: null, loading: Boolean(id), error: null }
  return { ...current, refresh: () => setNonce((n) => n + 1) }
}

/** Answer the one-time PubChem question (spec STRUCT-2). */
export function setOnlineStructures(online: 'on' | 'off') {
  return updateSettings({ structures: { online } })
}

const svgCache = new Map<string, Promise<string>>()

/** SVG depiction of a structure for the theme and size (cached). */
export function useStructureSvg(
  key: string | null | undefined,
  theme: 'light' | 'dark',
  width: number,
  height: number,
): string | null {
  const id = key ? `${key}|${theme}|${width}|${height}` : null
  const [state, setState] = useState<{ id: string; svg: string | null } | null>(null)
  useEffect(() => {
    if (!id || !key) return
    let request = svgCache.get(id)
    if (!request) {
      request = api().structureSvg(key, theme, width, height)
      svgCache.set(id, request)
      request.catch(() => svgCache.delete(id))
    }
    let alive = true
    request.then(
      (svg) => alive && setState({ id, svg }),
      () => alive && setState({ id, svg: null }),
    )
    return () => {
      alive = false
    }
  }, [id, key, theme, width, height])
  return state && state.id === id ? state.svg : null
}
