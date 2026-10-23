/**
 * Dropped files. A browser does not reveal where a dropped file lives, so only
 * shells that expose paths (the desktop app, milestone 5) can open it.
 */

import { notifyInfo } from '@/lib/notify'

import { openPath, showOpenDialog } from './openFile'

/** Path of a dropped file, when the shell exposes it (Tauri, Electron). */
function pathOf(file: File): string | null {
  const p = (file as File & { path?: unknown }).path
  return typeof p === 'string' && p ? p : null
}

export function droppedFiles(data: DataTransfer) {
  const files = Array.from(data.files)
  if (!files.length) return
  const paths = files.map(pathOf).filter((p): p is string => p !== null)
  if (paths.length) {
    for (const path of paths) void openPath(path)
    return
  }
  notifyInfo(
    `Choose ${files[0].name} in the file browser`,
    'The browser does not tell the app where a dropped file is stored.',
  )
  showOpenDialog()
}
