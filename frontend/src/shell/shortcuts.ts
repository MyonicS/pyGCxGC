/** Global keyboard shortcuts (spec SHELL-7). */

import { useEffect } from 'react'

import { isEditableTarget } from '@/lib/utils'
import { useSession } from '@/store/session'
import { deleteSelectedShape, redoMasks, undoMasks } from '@/store/masks'
import { resetZoom, setTool } from '@/store/view'

import { openCommandPalette } from './CommandPalette'
import { showOpenDialog } from './openFile'

/**
 * Single keys only act when no dialog or menu is open (closing ones still
 * sit in the DOM during their exit animation, with data-state="closed").
 */
function blockingOverlayOpen(): boolean {
  const open = '[data-state="open"]'
  return Boolean(
    document.querySelector(`[role="dialog"]${open}, [role="menu"]${open}, [role="listbox"]${open}`),
  )
}

export function useShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const modKey = e.ctrlKey || e.metaKey
      if (modKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        openCommandPalette()
        return
      }
      if (modKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'o') {
        e.preventDefault()
        showOpenDialog()
        return
      }
      if (isEditableTarget(e.target)) return
      // undo / redo of mask edits (spec MASK-5)
      if (modKey && !e.altKey && (e.key.toLowerCase() === 'z' || e.key.toLowerCase() === 'y')) {
        const docId = useSession.getState().activeId
        if (!docId || blockingOverlayOpen()) return
        e.preventDefault()
        const redo = e.key.toLowerCase() === 'y' || e.shiftKey
        void (redo ? redoMasks(docId) : undoMasks(docId))
        return
      }
      if (modKey || e.altKey) return
      if (blockingOverlayOpen()) return
      const activeId = useSession.getState().activeId
      switch (e.key) {
        case 'v':
        case 'V':
          setTool('select')
          break
        case 'h':
        case 'H':
          setTool('pan')
          break
        case 'z':
        case 'Z':
          setTool('zoom')
          break
        case 'r':
        case 'R':
          setTool('rect')
          break
        case 'l':
        case 'L':
          setTool('lasso')
          break
        case 'p':
        case 'P':
          setTool('polygon')
          break
        case '0':
          if (activeId) resetZoom(activeId)
          break
        case 'Delete':
        case 'Backspace':
          if (!deleteSelectedShape()) return
          break
        default:
          return
      }
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}
