/**
 * Mirror of the backend session (documents, libraries, jobs, settings and
 * per-document selection, peaks and masks). The backend owns this state
 * (spec ARCH-1); the store is refreshed from `/api/session` and kept current
 * by the event stream.
 */

import { create } from 'zustand'

import { Api } from '@/api/client'
import { ApiError, type ConnectionState, type Transport } from '@/api/transport'
import type {
  DocumentInfo,
  JobInfo,
  LibraryInfo,
  MaskSetInfo,
  PeakTable,
  SelectionInfo,
  SessionEvent,
  Settings,
} from '@/api/types'
import { notifyError, notifyWarnings } from '@/lib/notify'

export interface SessionState {
  api: Api | null
  connection: ConnectionState
  /** The first `/api/session` has been loaded. */
  ready: boolean
  /** Fatal start-up problem (no token, backend unreachable). */
  fatal: string | null
  version: string
  documents: Record<string, DocumentInfo>
  /** Tab order of the documents. */
  order: string[]
  activeId: string | null
  libraries: Record<string, LibraryInfo>
  jobs: Record<string, JobInfo>
  settings: Settings
  selections: Record<string, SelectionInfo | null>
  peaks: Record<string, PeakTable | null>
  masks: Record<string, MaskSetInfo>
  /** Last event revision per document (spec API-4). */
  revisions: Record<string, number>
}

export const useSession = create<SessionState>(() => ({
  api: null,
  connection: 'connecting',
  ready: false,
  fatal: null,
  version: '',
  documents: {},
  order: [],
  activeId: null,
  libraries: {},
  jobs: {},
  settings: {},
  selections: {},
  peaks: {},
  masks: {},
  revisions: {},
}))

const set = useSession.setState
const get = useSession.getState

export function api(): Api {
  const a = get().api
  if (!a) throw new Error('Not connected')
  return a
}

// ------------------------------------------------------------------ connection
let unsubscribe: (() => void) | null = null

export async function connect(transport: Transport): Promise<void> {
  unsubscribe?.()
  set({ api: new Api(transport), fatal: null })
  let firstOpen = true
  unsubscribe = transport.subscribe(handleEvent, (connection) => {
    set({ connection })
    // after a reconnect, events may have been missed: reload the session
    if (connection === 'open' && !firstOpen) void resync()
    if (connection === 'open') firstOpen = false
  })
  try {
    await resync()
  } catch (err) {
    set({ fatal: err instanceof Error ? err.message : String(err) })
  }
}

export function failStartup(message: string) {
  set({ fatal: message, connection: 'closed' })
}

/** Reload documents, libraries, jobs and settings from the backend. */
export async function resync(): Promise<void> {
  const info = await api().session()
  set((s) => {
    const documents = Object.fromEntries(info.documents.map((d) => [d.id, d]))
    const order = [
      ...s.order.filter((id) => id in documents),
      ...info.documents.map((d) => d.id).filter((id) => !s.order.includes(id)),
    ]
    const activeId = s.activeId && s.activeId in documents ? s.activeId : (order[0] ?? null)
    return {
      ready: true,
      version: info.version,
      documents,
      order,
      activeId,
      libraries: Object.fromEntries(info.libraries.map((l) => [l.id, l])),
      jobs: Object.fromEntries(info.jobs.map((j) => [j.id, j])),
      settings: info.settings as Settings,
    }
  })
  for (const doc of info.documents) void refreshDocumentState(doc)
}

/** Selection, peaks and masks of a document (after opening or a resync). */
async function refreshDocumentState(doc: DocumentInfo) {
  void refreshMasks(doc.id)
  if (doc.kind !== 'ms') return
  void refreshSelection(doc.id)
  if (doc.has_peaks) void refreshPeaks(doc.id)
}

async function refreshSelection(docId: string) {
  try {
    const selection = await api().selection(docId)
    set((s) => ({ selections: { ...s.selections, [docId]: selection } }))
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) console.warn(err)
  }
}

async function refreshPeaks(docId: string) {
  try {
    const table = await api().peaks(docId)
    set((s) => ({ peaks: { ...s.peaks, [docId]: table } }))
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) console.warn(err)
  }
}

async function refreshMasks(docId: string) {
  try {
    const masks = await api().masks(docId)
    set((s) => ({ masks: { ...s.masks, [docId]: masks } }))
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) console.warn(err)
  }
}

// ------------------------------------------------------------------ events
const jobWaiters = new Map<string, Array<(job: JobInfo) => void>>()
const finishedJobTimers = new Map<string, ReturnType<typeof setTimeout>>()

function isFinished(job: JobInfo) {
  return job.status === 'done' || job.status === 'failed' || job.status === 'cancelled'
}

export function handleEvent(event: SessionEvent): void {
  const docId = event.document_id
  if (docId) set((s) => ({ revisions: { ...s.revisions, [docId]: event.revision } }))
  switch (event.type) {
    case 'job.progress':
    case 'job.done':
    case 'job.failed':
      onJob(event.job)
      break
    case 'document.updated':
      onDocument(event.document_id, event.document)
      break
    case 'selection.changed':
      if (docId) set((s) => ({ selections: { ...s.selections, [docId]: event.selection } }))
      break
    case 'peaks.updated':
      if (docId) void refreshPeaks(docId)
      break
    case 'masks.updated':
      if (docId) void refreshMasks(docId)
      break
    case 'library.updated':
      set((s) => {
        const libraries = { ...s.libraries }
        if (event.library.status === 'removed') delete libraries[event.library.id]
        else libraries[event.library.id] = event.library
        return { libraries }
      })
      break
  }
}

function onJob(job: JobInfo) {
  set((s) => ({ jobs: { ...s.jobs, [job.id]: job } }))
  if (!isFinished(job)) return
  for (const resolve of jobWaiters.get(job.id) ?? []) resolve(job)
  jobWaiters.delete(job.id)
  if (job.status === 'failed') {
    notifyError(job.label, job.error ?? 'Failed', job.traceback)
  } else if (job.status === 'done' && job.warnings.length) {
    notifyWarnings(job.label, job.warnings)
  }
  // keep finished jobs briefly so the status bar can show the outcome
  if (!finishedJobTimers.has(job.id)) {
    finishedJobTimers.set(
      job.id,
      setTimeout(() => {
        finishedJobTimers.delete(job.id)
        set((s) => {
          const jobs = { ...s.jobs }
          delete jobs[job.id]
          return { jobs }
        })
      }, 2500),
    )
  }
}

function onDocument(docId: string | null, doc: DocumentInfo | null) {
  if (!docId) return
  if (doc === null) {
    set((s) => {
      const documents = { ...s.documents }
      delete documents[docId]
      const index = s.order.indexOf(docId)
      const order = s.order.filter((id) => id !== docId)
      const activeId =
        s.activeId === docId ? (order[Math.min(index, order.length - 1)] ?? null) : s.activeId
      return { documents, order, activeId }
    })
    return
  }
  const isNew = !(docId in get().documents)
  set((s) => ({
    documents: { ...s.documents, [docId]: doc },
    order: s.order.includes(docId) ? s.order : [...s.order, docId],
    activeId: s.activeId ?? docId,
  }))
  if (isNew) void refreshDocumentState(doc)
}

/** Resolve when a job has finished (whatever its status). */
export function waitForJob(job: JobInfo): Promise<JobInfo> {
  if (isFinished(job)) return Promise.resolve(job)
  const known = get().jobs[job.id]
  if (known && isFinished(known)) return Promise.resolve(known)
  set((s) => ({ jobs: { ...s.jobs, [job.id]: s.jobs[job.id] ?? job } }))
  return new Promise((resolve) => {
    const list = jobWaiters.get(job.id) ?? []
    list.push(resolve)
    jobWaiters.set(job.id, list)
  })
}

// ------------------------------------------------------------------ actions
export function setActiveDocument(docId: string) {
  if (docId in get().documents) set({ activeId: docId })
}

export function moveDocument(docId: string, toIndex: number) {
  set((s) => {
    const order = s.order.filter((id) => id !== docId)
    order.splice(Math.max(0, Math.min(toIndex, order.length)), 0, docId)
    return { order }
  })
}

export async function closeDocument(docId: string) {
  try {
    await api().closeDocument(docId)
  } catch (err) {
    notifyError('Close document', err)
  }
}

/** Merge `changes` into the settings (locally at once, then on the backend). */
export async function updateSettings(changes: Settings): Promise<void> {
  set((s) => ({ settings: deepMerge(s.settings, changes) }))
  try {
    const settings = await api().updateSettings(changes)
    set({ settings })
  } catch (err) {
    notifyError('Save settings', err)
  }
}

export async function cancelJob(jobId: string) {
  try {
    const job = await api().cancelJob(jobId)
    onJob(job)
  } catch (err) {
    notifyError('Cancel job', err)
  }
}

export async function setLibraryActive(libraryId: string, exclusive = true) {
  try {
    const lib = await api().setLibraryActive(libraryId, true, exclusive)
    set((s) => ({ libraries: { ...s.libraries, [lib.id]: lib } }))
    // exclusive activation deactivates the others
    if (exclusive) {
      const libs = await api().libraries()
      set({ libraries: Object.fromEntries(libs.map((l) => [l.id, l])) })
    }
  } catch (err) {
    notifyError('Select library', err)
  }
}

function deepMerge<T extends Record<string, unknown>>(target: T, changes: Record<string, unknown>) {
  const out: Record<string, unknown> = { ...target }
  for (const [key, value] of Object.entries(changes)) {
    const current = out[key]
    if (isPlainObject(value) && isPlainObject(current)) out[key] = deepMerge(current, value)
    else out[key] = value
  }
  return out as T
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

// ------------------------------------------------------------------ selectors
export const useActiveDocument = () =>
  useSession((s) => (s.activeId ? s.documents[s.activeId] : undefined))
