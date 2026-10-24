/**
 * Project files (`*.gcxgc`, spec §9): documents (paths, never data),
 * processing, masks, peak edits, selections, view state and layouts.
 */

import { toast } from 'sonner'

import { notifyError, notifyInfo, notifyWarnings } from '@/lib/notify'
import { basename } from '@/lib/utils'
import { api, useSession, waitForJob } from '@/store/session'
import { flushViews } from '@/store/viewSync'

import { pickPath } from './PathDialog'

export const PROJECT_EXTENSION = '.gcxgc'

export const isProjectPath = (path: string) => path.toLowerCase().endsWith(PROJECT_EXTENSION)

/** Save the project (to a new file with `as`, or when it has none yet). */
export async function saveProject(as = false): Promise<string | null> {
  const { project, documents, order } = useSession.getState()
  let path: string | undefined = project?.path
  if (as || !path) {
    const first = order.map((id) => documents[id]).find(Boolean)
    const stem = project?.name ?? first?.name.replace(/\.[^.]+$/, '') ?? 'project'
    const picked = await pickPath({
      title: 'Save project',
      description:
        'A project stores file paths, processing, masks, peak edits and the view; never data.',
      mode: 'save',
      extensions: [PROJECT_EXTENSION],
      defaultName: `${stem}${PROJECT_EXTENSION}`,
      confirm: 'Save',
    })
    if (!picked) return null
    path = picked
  }
  try {
    await flushViews()
    const saved = await api().saveProject(path)
    if (saved.warnings.length) notifyWarnings(`Saved ${basename(saved.path)}`, saved.warnings)
    else notifyInfo(`Saved ${basename(saved.path)}`)
    return saved.path
  } catch (err) {
    notifyError('Save project', err)
    return null
  }
}

/**
 * Open a project. It replaces the open documents, so with documents open
 * the user confirms in a (non-blocking) toast first.
 */
export async function openProject(path: string, confirmed = false): Promise<boolean> {
  const n = useSession.getState().order.length
  if (n && !confirmed) {
    toast(`Open ${basename(path)}?`, {
      description: `This closes the ${n} open document${n > 1 ? 's' : ''}; unsaved masks and peak edits are lost.`,
      action: { label: 'Open', onClick: () => void openProject(path, true) },
      cancel: { label: 'Cancel', onClick: () => undefined },
      duration: 15000,
    })
    return false
  }
  try {
    const { job } = await api().openProject(path)
    const done = await waitForJob(job)
    return done.status === 'done'
  } catch (err) {
    notifyError(`Open ${basename(path)}`, err)
    return false
  }
}

/** Close all documents and forget the project. */
export async function closeProject() {
  try {
    await api().closeProject()
  } catch (err) {
    notifyError('Close project', err)
  }
}
