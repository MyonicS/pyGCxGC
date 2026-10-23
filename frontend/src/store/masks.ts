/**
 * Mask commands shared by the chromatogram view, the Masks and Integration
 * panels and the shortcuts (spec §6.2). The masks live in Python; every
 * command is one API call whose response updates the store.
 */

import type { DownloadedFile } from '@/api/transport'
import type { ExportRequest, MaskImport, MaskInfo, MaskUpdate, Shape } from '@/api/types'
import { notifyError, notifyInfo } from '@/lib/notify'

import { maskAction, queueMaskEdit, runMaskAction, useSession } from './session'
import { resolveActiveMask, selectShape, setActiveMask, useView } from './view'

const NO_MASKS: MaskInfo[] = []

export function useMasks(docId: string | undefined): MaskInfo[] {
  return useSession((s) => (docId ? s.masks[docId]?.masks : undefined) ?? NO_MASKS)
}

/** The mask new shapes go into (spec MASK-3). */
export function useActiveMaskId(docId: string | undefined): string | undefined {
  const masks = useMasks(docId)
  const chosen = useView((s) => (docId ? s.activeMask[docId] : undefined))
  return resolveActiveMask(
    chosen,
    masks.map((m) => m.id),
  )
}

function activeMaskId(docId: string): string | undefined {
  const masks = useSession.getState().masks[docId]?.masks ?? []
  return resolveActiveMask(
    useView.getState().activeMask[docId],
    masks.map((m) => m.id),
  )
}

/** Create a mask and make it the active one. */
export function newMask(docId: string): Promise<string | undefined> {
  return queueMaskEdit(docId, () => createActiveMask(docId))
}

async function createActiveMask(docId: string): Promise<string | undefined> {
  const info = await runMaskAction(docId, 'New mask', (api) => api.createMask(docId))
  const id = info?.masks.at(-1)?.id
  if (id) setActiveMask(docId, id)
  return id
}

/**
 * Add (or subtract) a drawn shape to the active mask, creating one if needed.
 * The target is resolved when the edit runs, after the edits before it.
 */
export function drawShape(docId: string, shape: Shape, mode: 'add' | 'subtract') {
  return queueMaskEdit(docId, async () => {
    let maskId = activeMaskId(docId)
    if (!maskId) {
      if (mode === 'subtract') {
        notifyInfo('Nothing to subtract from', 'Draw a shape in add mode first.')
        return
      }
      maskId = await createActiveMask(docId)
      if (!maskId) return
    }
    const target = maskId
    await runMaskAction(docId, mode === 'add' ? 'Add shape' : 'Subtract shape', (api) =>
      api.addShape(docId, target, shape, mode),
    )
  })
}

export function updateMask(docId: string, maskId: string, changes: MaskUpdate) {
  return maskAction(docId, 'Update mask', (api) => api.updateMask(docId, maskId, changes))
}

export function duplicateMask(docId: string, maskId: string) {
  return queueMaskEdit(docId, async () => {
    const info = await runMaskAction(docId, 'Duplicate mask', (api) =>
      api.duplicateMask(docId, maskId),
    )
    // the copy follows the original
    const index = info?.masks.findIndex((m) => m.id === maskId) ?? -1
    const copy = index >= 0 ? info?.masks[index + 1] : undefined
    if (copy) setActiveMask(docId, copy.id)
  })
}

export function deleteMask(docId: string, maskId: string) {
  return queueMaskEdit(docId, async () => {
    const masks = useSession.getState().masks[docId]?.masks ?? []
    const index = masks.findIndex((m) => m.id === maskId)
    const info = await runMaskAction(docId, 'Delete mask', (api) => api.deleteMask(docId, maskId))
    if (info) {
      const next = info.masks[Math.min(index, info.masks.length - 1)]
      setActiveMask(docId, next?.id)
    }
  })
}

export function moveMask(docId: string, maskId: string, position: number) {
  return updateMask(docId, maskId, { position })
}

export function replaceShape(docId: string, maskId: string, opId: string, shape: Shape) {
  return maskAction(docId, 'Edit shape', (api) => api.replaceShape(docId, maskId, opId, shape))
}

export async function deleteShape(docId: string, maskId: string, opId: string) {
  const info = await maskAction(docId, 'Delete shape', (api) =>
    api.deleteShape(docId, maskId, opId),
  )
  if (info) selectShape(null)
}

/** Delete the selected shape (Delete key). */
export function deleteSelectedShape(): boolean {
  const sel = useView.getState().selectedShape
  if (!sel) return false
  void deleteShape(sel.docId, sel.maskId, sel.opId)
  return true
}

export function undoMasks(docId: string) {
  selectShape(null)
  return maskAction(docId, 'Undo', (api) => api.undoMasks(docId))
}

export function redoMasks(docId: string) {
  selectShape(null)
  return maskAction(docId, 'Redo', (api) => api.redoMasks(docId))
}

/** Import masks from a file or folder, another document or a mask set (spec INT-3). */
export function importMasks(docId: string, req: MaskImport, label = 'Import masks') {
  return queueMaskEdit(docId, async () => {
    const before = new Set((useSession.getState().masks[docId]?.masks ?? []).map((m) => m.id))
    const info = await runMaskAction(docId, label, (api) => api.importMasks(docId, req))
    const added = info?.masks.filter((m) => !before.has(m.id)) ?? []
    if (added.length) setActiveMask(docId, added.at(-1)!.id)
    return added
  })
}

/** Export as a browser download (spec INT-2). */
export async function downloadExport(docId: string, req: ExportRequest, label: string) {
  try {
    const api = useSession.getState().api
    if (!api) return
    saveFile(await api.exportFile(docId, req))
  } catch (err) {
    notifyError(label, err)
  }
}

export function saveFile(file: DownloadedFile) {
  const url = URL.createObjectURL(new Blob([file.data], { type: file.mediaType }))
  const a = document.createElement('a')
  a.href = url
  a.download = file.filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
