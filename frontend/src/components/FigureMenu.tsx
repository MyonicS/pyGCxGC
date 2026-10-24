/**
 * Menu entries that save a figure (spec VIEW-12): the theme of the figure,
 * light or dark (the app's theme until one is chosen; the choice is
 * remembered), then PNG or SVG.
 */

import { Moon, Sun } from 'lucide-react'

import {
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'
import type { FigureFormat, FigureTheme } from '@/lib/figure'
import { useResolvedTheme } from '@/shell/theme'
import { updateSettings, useSession } from '@/store/session'

export function useFigureTheme(): [FigureTheme, (theme: FigureTheme) => void] {
  const saved = useSession((s) => s.settings.figures?.theme)
  const app = useResolvedTheme()
  const theme = saved === 'light' || saved === 'dark' ? saved : app
  return [theme, (t) => void updateSettings({ figures: { theme: t } })]
}

export function FigureMenuItems({
  onSave,
}: {
  onSave: (format: FigureFormat, theme: FigureTheme) => void
}) {
  const [theme, setTheme] = useFigureTheme()
  return (
    <>
      <DropdownMenuLabel>Save figure</DropdownMenuLabel>
      <DropdownMenuRadioGroup
        value={theme}
        onValueChange={(v) => setTheme(v as FigureTheme)}
        aria-label="Figure theme"
      >
        {/* choosing the theme keeps the menu open */}
        <DropdownMenuRadioItem value="light" onSelect={(e) => e.preventDefault()}>
          <Sun /> Light
        </DropdownMenuRadioItem>
        <DropdownMenuRadioItem value="dark" onSelect={(e) => e.preventDefault()}>
          <Moon /> Dark
        </DropdownMenuRadioItem>
      </DropdownMenuRadioGroup>
      <DropdownMenuSeparator />
      <DropdownMenuItem onSelect={() => onSave('png', theme)}>
        PNG (2× pixel density)
      </DropdownMenuItem>
      <DropdownMenuItem onSelect={() => onSave('svg', theme)}>
        SVG (vector axes and labels)
      </DropdownMenuItem>
    </>
  )
}
