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
  IntegrationResult,
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
  integration: Record<string, IntegrationState | undefined>
  /** Last event revision per document (spec API-4). */
  revisions: Record<string, number>
}

export interface IntegrationState {
  result: IntegrationResult | null
  /** Why the masks cannot be integrated (e.g. a raster mask of another shape). */
  error: string | null
  /** Mask revision the result belongs to. */
  masksRevision: number
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
  integration: {},
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
    applyMasks(docId, await api().masks(docId), true)
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) console.warn(err)
  }
}

/**
 * Store a mask set and its integration (both come with every mask response).
 * Older revisions are ignored unless `force` (after re-processing the
 * revision stays the same but pixel counts and volumes change).
 */
function applyMasks(docId: string, info: MaskSetInfo, force = false) {
  set((s) => {
    const current = s.masks[docId]
    if (!force && current && current.revision > info.revision) return {}
    const integration: IntegrationState = {
      result: info.integration ?? null,
      error: info.integration ? null : (info.error ?? null),
      masksRevision: info.revision,
    }
    return {
      masks: { ...s.masks, [docId]: info },
      integration: { ...s.integration, [docId]: integration },
    }
  })
}

const maskQueues = new Map<string, Promise<unknown>>()

/**
 * Run the mask edits of a document one after the other, in the order they
 * were made (requests could otherwise overtake each other), so an edit sees
 * the result of the previous one (e.g. the mask "New mask" made active).
 */
export function queueMaskEdit<T>(docId: string, task: () => Promise<T>): Promise<T> {
  const previous = maskQueues.get(docId) ?? Promise.resolve()
  const next = previous.then(task, task)
  maskQueues.set(
    docId,
    next.catch(() => undefined),
  )
  return next
}

/**
 * Run a mask edit (spec MASK-*) in the document's queue: the returned mask
 * set is shown at once and the integration refreshed. Errors are toasts;
 * returns `null` then.
 */
export function maskAction(
  docId: string,
  label: string,
  fn: (api: Api) => Promise<MaskSetInfo>,
): Promise<MaskSetInfo | null> {
  return queueMaskEdit(docId, () => runMaskAction(docId, label, fn))
}

/** {@link maskAction} without the queue (inside a queued task). */
export async function runMaskAction(
  docId: string,
  label: string,
  fn: (api: Api) => Promise<MaskSetInfo>,
): Promise<MaskSetInfo | null> {
  inFlight.set(docId, (inFlight.get(docId) ?? 0) + 1)
  try {
    const info = await fn(api())
    applyMasks(docId, info)
    return info
  } catch (err) {
    notifyError(label, err)
    return null
  } finally {
    const left = (inFlight.get(docId) ?? 1) - 1
    inFlight.set(docId, left)
    // a change made elsewhere meanwhile (e.g. from a notebook)
    const missed = missedRevision.get(docId) ?? -1
    if (left === 0 && missed > (get().masks[docId]?.revision ?? -1)) void refreshMasks(docId)
    if (left === 0) missedRevision.delete(docId)
  }
}

/**
 * Edits in flight per document: their own `masks.updated` events are not
 * refetched (the response brings the state), only later revisions.
 */
const inFlight = new Map<string, number>()
const missedRevision = new Map<string, number>()

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
    case 'masks.updated': {
      if (!docId) break
      const revision = event.masks_revision ?? Infinity
      if ((inFlight.get(docId) ?? 0) > 0) {
        missedRevision.set(docId, Math.max(missedRevision.get(docId) ?? -1, revision))
      } else if (revision > (get().masks[docId]?.revision ?? -1)) {
        void refreshMasks(docId)
      }
      break
    }
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
      const masks = { ...s.masks }
      const integration = { ...s.integration }
      delete masks[docId]
      delete integration[docId]
      return { documents, order, activeId, masks, integration }
    })
    return
  }
  const isNew = !(docId in get().documents)
  set((s) => ({
    documents: { ...s.documents, [docId]: doc },
    order: s.order.includes(docId) ? s.order : [...s.order, docId],
    activeId: s.activeId ?? docId,
  }))
  // new data (processing, signal): the same masks cover other values
  if (isNew) void refreshDocumentState(doc)
  else void refreshMasks(docId)
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
