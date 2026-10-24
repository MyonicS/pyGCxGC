/**
 * Top bar (spec SHELL-1): open file and recent files, document tabs, the
 * active library, theme and the app menu.
 */

import {
  ChevronDown,
  Command,
  FileText,
  FolderOpen,
  LayoutGrid,
  Monitor,
  Moon,
  MoreHorizontal,
  PanelsTopLeft,
  Save,
  Settings2,
  Sun,
  X,
  XSquare,
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
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { ThemeSetting } from '@/api/types'
import { basename, cn } from '@/lib/utils'
import {
  closeDocument,
  moveDocument,
  openLogFolder,
  setActiveDocument,
  useSession,
} from '@/store/session'

import { LibraryPicker } from '@/ms/LibraryPicker'

import { openCommandPalette } from './CommandPalette'
import { openPath, showOpenDialog } from './openFile'
import { closeProject, isProjectPath, saveProject } from './project'
import { openSettings } from './SettingsDialog'
import { setTheme, useThemeSetting } from './theme'
import { COMPACT, PANELS, resetLayout, showPanel, type PanelId } from './Workspace'

const NO_FILES: string[] = []

export const mod =
  typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform) ? '⌘' : 'Ctrl+'

export function TopBar() {
  return (
    <header className="flex h-10 shrink-0 items-center gap-1 border-b bg-chrome px-2">
      <div className="flex items-center gap-1.5 pr-2 pl-1 select-none" aria-label="pyGCxGC Studio">
        <Logo />
        <span className="text-sm font-semibold tracking-tight">pyGCxGC</span>
        {!COMPACT && <span className="text-sm text-muted-foreground">Studio</span>}
      </div>
      <ProjectName />
      <OpenMenu />
      <DocumentTabs />
      <div className="ml-auto flex items-center gap-1">
        <LibraryPicker />
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
        <TooltipContent>Open a chromatogram or project ({mod}O)</TooltipContent>
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
              {isProjectPath(path) && <FileText className="text-primary" />}
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

/** The project's name; a click saves it. */
function ProjectName() {
  const project = useSession((s) => s.project)
  if (!project) return null
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="max-w-40 truncate rounded-md px-1.5 py-0.5 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
          onClick={() => void saveProject()}
          data-testid="project-name"
        >
          {project.name}
        </button>
      </TooltipTrigger>
      <TooltipContent>
        Project {project.path} – click to save ({mod}S)
      </TooltipContent>
    </Tooltip>
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
  const hasDocuments = useSession((s) => s.order.length > 0)
  const project = useSession((s) => s.project)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Menu">
          <MoreHorizontal />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuItem disabled={!hasDocuments} onSelect={() => void saveProject()}>
          <Save /> Save project
          <DropdownMenuShortcut>{mod}S</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!hasDocuments} onSelect={() => void saveProject(true)}>
          <Save /> Save project as…
        </DropdownMenuItem>
        <DropdownMenuItem disabled={!hasDocuments && !project} onSelect={() => void closeProject()}>
          <XSquare /> Close all
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => openCommandPalette()}>
          <Command /> Command palette
          <DropdownMenuShortcut>{mod}K</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => openSettings()}>
          <Settings2 /> Settings
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <PanelsTopLeft /> Panels
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            {(Object.keys(PANELS) as PanelId[]).map((id) => (
              <DropdownMenuItem key={id} onSelect={() => showPanel(id)}>
                {PANELS[id].title}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuItem onSelect={() => resetLayout()}>
          <LayoutGrid /> Reset layout
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
          Help
        </DropdownMenuLabel>
        <DropdownMenuItem onSelect={() => void openLogFolder()}>
          <FileText /> Open log folder
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
