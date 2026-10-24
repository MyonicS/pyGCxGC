/** Opening chromatograms (spec SHELL-5). */

import { create } from 'zustand'

import type { ProcessingParams } from '@/api/types'
import { notifyError } from '@/lib/notify'
import { basename } from '@/lib/utils'
import { api, setActiveDocument, updateSettings, useSession, waitForJob } from '@/store/session'

import { openProject } from './project'

interface OpenDialogState {
  open: boolean
  /** File to preselect (e.g. a recent FID file that needs a modulation time). */
  path: string | null
}

export const useOpenDialog = create<OpenDialogState>(() => ({ open: false, path: null }))

export function showOpenDialog(path: string | null = null) {
  useOpenDialog.setState({ open: true, path })
}

export function hideOpenDialog() {
  useOpenDialog.setState({ open: false, path: null })
}

/**
 * Open a file. FID files need a modulation time: without one (and without a
 * default in the settings) the open dialog is shown for the file. Projects
 * (`.gcxgc`) are opened as projects.
 */
export async function openPath(path: string, params?: ProcessingParams): Promise<string | null> {
  try {
    if (!params?.modulation_time) {
      const probe = await api().probe(path)
      if (probe.error) {
        notifyError(`Open ${basename(path)}`, probe.error)
        return null
      }
      if (probe.kind === 'project') {
        await openProject(path)
        return null
      }
      if (probe.kind === 'fid' && probe.needs_modulation_time) {
        showOpenDialog(path)
        return null
      }
    }
    const { job } = await api().openDocument({ path, params: params ?? {} })
    const done = await waitForJob(job)
    const docId = done.status === 'done' ? (done.result?.document_id as string | undefined) : null
    if (docId) setActiveDocument(docId)
    return docId ?? null
  } catch (err) {
    notifyError(`Open ${basename(path)}`, err)
    return null
  }
}

/** Remember the FID modulation time as the default for the next files. */
export function rememberModulationTime(seconds: number) {
  const current = useSession.getState().settings.fid?.modulation_time
  if (current !== seconds) void updateSettings({ fid: { modulation_time: seconds } })
}

export const SUPPORTED_EXTENSIONS = ['.csv', '.txt', '.cdf', '.jdx', '.dx', '.jcamp', '.gcxgc']

export function isSupported(name: string): boolean {
  const lower = name.toLowerCase()
  return SUPPORTED_EXTENSIONS.some((ext) => lower.endsWith(ext))
}
