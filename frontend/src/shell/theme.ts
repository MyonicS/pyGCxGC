/** Light, dark and system themes (spec THEME-1); the choice is saved in the settings. */

import { useEffect, useSyncExternalStore } from 'react'

import type { ThemeSetting } from '@/api/types'
import { updateSettings, useSession } from '@/store/session'

const query = () => window.matchMedia('(prefers-color-scheme: dark)')

function subscribeSystem(callback: () => void) {
  const mq = query()
  mq.addEventListener('change', callback)
  return () => mq.removeEventListener('change', callback)
}

export function useThemeSetting(): ThemeSetting {
  const theme = useSession((s) => s.settings.theme)
  return theme === 'light' || theme === 'dark' ? theme : 'system'
}

/** The theme in force (`system` resolved). */
export function useResolvedTheme(): 'light' | 'dark' {
  const setting = useThemeSetting()
  const systemDark = useSyncExternalStore(subscribeSystem, () => query().matches)
  return setting === 'system' ? (systemDark ? 'dark' : 'light') : setting
}

/** Apply the theme to the document. */
export function useApplyTheme() {
  const resolved = useResolvedTheme()
  useEffect(() => {
    document.documentElement.classList.toggle('dark', resolved === 'dark')
  }, [resolved])
  return resolved
}

export function setTheme(theme: ThemeSetting) {
  void updateSettings({ theme })
}

/** light -> dark -> system -> light */
export function cycleTheme(current: ThemeSetting) {
  setTheme(current === 'light' ? 'dark' : current === 'dark' ? 'system' : 'light')
}
