/** Global keyboard shortcuts (spec SHELL-7). */

import { useEffect } from 'react'

import { isEditableTarget } from '@/lib/utils'
import { useSession } from '@/store/session'
import { resetZoom, setTool } from '@/store/view'

import { openCommandPalette } from './CommandPalette'
import { showOpenDialog } from './openFile'

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
      if (modKey || e.altKey || isEditableTarget(e.target)) return
      // single keys only when no dialog or menu is open (closing ones still
      // sit in the DOM during their exit animation, with data-state="closed")
      const open = '[data-state="open"]'
      if (
        document.querySelector(
          `[role="dialog"]${open}, [role="menu"]${open}, [role="listbox"]${open}`,
        )
      )
        return
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
        case '0':
          if (activeId) resetZoom(activeId)
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
