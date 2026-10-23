/**
 * Top bar (spec SHELL-1): open file and recent files, document tabs, the
 * active library, theme and the app menu.
 */

import {
  BookMarked,
  ChevronDown,
  Command,
  FolderOpen,
  LayoutGrid,
  Monitor,
  Moon,
  MoreHorizontal,
  Settings2,
  Sun,
  X,
} from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { ThemeSetting } from '@/api/types'
import { basename, cn } from '@/lib/utils'
import {
  closeDocument,
  moveDocument,
  setActiveDocument,
  setLibraryActive,
  useSession,
} from '@/store/session'

import { openCommandPalette } from './CommandPalette'
import { openPath, showOpenDialog } from './openFile'
import { openSettings } from './SettingsDialog'
import { setTheme, useThemeSetting } from './theme'
import { resetLayout } from './Workspace'

const NO_FILES: string[] = []

export const mod =
  typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform) ? '⌘' : 'Ctrl+'

export function TopBar() {
  return (
    <header className="flex h-10 shrink-0 items-center gap-1 border-b bg-chrome px-2">
      <div className="flex items-center gap-1.5 pr-2 pl-1 select-none" aria-label="pyGCxGC Studio">
        <Logo />
        <span className="text-sm font-semibold tracking-tight">pyGCxGC</span>
        <span className="text-sm text-muted-foreground">Studio</span>
      </div>
      <OpenMenu />
      <DocumentTabs />
      <div className="ml-auto flex items-center gap-1">
        <LibrarySelector />
        <ThemeMenu />
        <AppMenu />
      </div>
    </header>
  )
}

function Logo() {
  return (
    <svg viewBox="0 0 16 16" className="size-4 text-primary" aria-hidden>
      <rect x="1" y="1" width="14" height="14" rx="3" className="fill-current opacity-20" />
      <ellipse cx="6" cy="9.5" rx="2.2" ry="1.4" className="fill-current" />
      <ellipse cx="10.5" cy="6" rx="1.6" ry="1.1" className="fill-current opacity-70" />
    </svg>
  )
}

function OpenMenu() {
  const recent = useSession((s) => s.settings.recent_files ?? NO_FILES)
  return (
    <div className="flex items-center">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="rounded-r-none"
            onClick={() => showOpenDialog()}
            data-testid="open-file"
          >
            <FolderOpen />
            Open
          </Button>
        </TooltipTrigger>
        <TooltipContent>Open a chromatogram ({mod}O)</TooltipContent>
      </Tooltip>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon-sm"
            className="rounded-l-none"
            aria-label="Recent files"
          >
            <ChevronDown />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-96">
          <DropdownMenuLabel>Recent files</DropdownMenuLabel>
          {recent.length === 0 && <DropdownMenuItem disabled>No recent files</DropdownMenuItem>}
          {recent.map((path) => (
            <DropdownMenuItem key={path} onSelect={() => void openPath(path)} title={path}>
              <span className="truncate">{basename(path)}</span>
              <span className="ml-auto max-w-48 truncate text-xs text-muted-foreground">
                {path}
              </span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

function DocumentTabs() {
  const order = useSession((s) => s.order)
  const documents = useSession((s) => s.documents)
  const activeId = useSession((s) => s.activeId)
  const [dragged, setDragged] = useState<string | null>(null)
  return (
    <nav
      className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto px-1"
      aria-label="Documents"
      role="tablist"
    >
      {order.map((id, index) => {
        const doc = documents[id]
        if (!doc) return null
        const active = id === activeId
        return (
          <div
            key={id}
            role="tab"
            aria-selected={active}
            tabIndex={0}
            draggable
            onDragStart={() => setDragged(id)}
            onDragEnd={() => setDragged(null)}
            onDragOver={(e) => {
              if (dragged && dragged !== id) e.preventDefault()
            }}
            onDrop={() => dragged && moveDocument(dragged, index)}
            onClick={() => setActiveDocument(id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') setActiveDocument(id)
            }}
            onAuxClick={(e) => {
              if (e.button === 1) void closeDocument(id)
            }}
            title={doc.path ?? doc.name}
            className={cn(
              'group flex h-7 max-w-56 shrink-0 cursor-default items-center gap-1.5 rounded-md border border-transparent pr-1 pl-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/50',
              active
                ? 'border-border bg-panel text-foreground shadow-xs'
                : 'text-muted-foreground hover:bg-accent hover:text-foreground',
            )}
            data-testid="document-tab"
          >
            <span
              className={cn(
                'rounded-sm px-1 font-mono text-[9px] font-semibold',
                doc.kind === 'ms' ? 'bg-primary/15 text-primary' : 'bg-success/15 text-success',
              )}
            >
              {doc.kind.toUpperCase()}
            </span>
            <span className="truncate">{doc.name}</span>
            <button
              type="button"
              aria-label={`Close ${doc.name}`}
              className="rounded-sm p-0.5 opacity-60 group-hover:opacity-100 hover:bg-muted"
              onClick={(e) => {
                e.stopPropagation()
                void closeDocument(id)
              }}
            >
              <X className="size-3" />
            </button>
          </div>
        )
      })}
    </nav>
  )
}

function LibrarySelector() {
  const libraries = useSession((s) => s.libraries)
  const list = Object.values(libraries)
  const active = list.find((l) => l.active)
  if (list.length === 0) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex h-7 items-center gap-1.5 px-2 text-xs text-muted-foreground">
            <BookMarked className="size-3.5" />
            No library
          </div>
        </TooltipTrigger>
        <TooltipContent>Libraries are added in the Libraries panel (milestone 3)</TooltipContent>
      </Tooltip>
    )
  }
  return (
    <Select value={active?.id ?? ''} onValueChange={(id) => void setLibraryActive(id, true)}>
      <SelectTrigger
        size="sm"
        className="h-7 max-w-56 border-transparent bg-transparent text-xs shadow-none hover:bg-accent dark:bg-transparent"
        aria-label="Active library"
      >
        <BookMarked className="size-3.5" />
        <SelectValue placeholder="Select library" />
      </SelectTrigger>
      <SelectContent align="end">
        {list.map((lib) => (
          <SelectItem key={lib.id} value={lib.id} disabled={lib.status !== 'ready'}>
            <span className="truncate">{lib.name}</span>
            <span className="text-xs text-muted-foreground">
              {lib.status === 'ready'
                ? `${lib.n_spectra?.toLocaleString() ?? ''} spectra`
                : lib.status}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

const THEME_ICON: Record<ThemeSetting, React.ReactNode> = {
  light: <Sun />,
  dark: <Moon />,
  system: <Monitor />,
}

function ThemeMenu() {
  const theme = useThemeSetting()
  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label="Theme" data-testid="theme-menu">
              {THEME_ICON[theme]}
            </Button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent>Theme</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={theme} onValueChange={(v) => setTheme(v as ThemeSetting)}>
          <DropdownMenuRadioItem value="light">
            <Sun /> Light
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">
            <Moon /> Dark
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">
            <Monitor /> System
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function AppMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Menu">
          <MoreHorizontal />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuItem onSelect={() => openCommandPalette()}>
          <Command /> Command palette
          <DropdownMenuShortcut>{mod}K</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => openSettings()}>
          <Settings2 /> Settings
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => resetLayout()}>
          <LayoutGrid /> Reset layout
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
