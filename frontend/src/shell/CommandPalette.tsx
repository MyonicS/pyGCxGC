/** Command palette, Ctrl/Cmd+K (spec SHELL-6), listing the keyboard shortcuts (SHELL-7). */

import { create } from 'zustand'

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from '@/components/ui/command'
import { exportChromatogram } from '@/chromatogram/figure'
import { COLORMAPS } from '@/lib/colormaps'
import { SCALING_LABELS, SCALINGS } from '@/lib/scaling'
import { basename } from '@/lib/utils'
import {
  copySpectrum,
  detectPeaks,
  downloadPeaks,
  downloadSpectrum,
  setPointSettings,
  usePointSettings,
} from '@/store/ms'
import {
  closeDocument,
  openLogFolder,
  setActiveDocument,
  setLibraryActive,
  useSession,
} from '@/store/session'
import {
  INTERPOLATION_LABELS,
  INTERPOLATIONS,
  resetZoom,
  setTool,
  updateDisplay,
} from '@/store/view'

import { openPath, showOpenDialog } from './openFile'
import { closeProject, saveProject } from './project'
import { openSettings } from './SettingsDialog'
import { setTheme } from './theme'
import { mod } from './TopBar'
import { resetLayout, showPanel } from './Workspace'

const usePalette = create<{ open: boolean }>(() => ({ open: false }))

export function openCommandPalette() {
  usePalette.setState({ open: true })
}

const NO_FILES: string[] = []

export function CommandPalette() {
  const open = usePalette((s) => s.open)
  const documents = useSession((s) => s.documents)
  const order = useSession((s) => s.order)
  const activeId = useSession((s) => s.activeId)
  const libraries = useSession((s) => s.libraries)
  const recent = useSession((s) => s.settings.recent_files ?? NO_FILES)
  const project = useSession((s) => s.project)
  const activeDoc = activeId ? documents[activeId] : undefined
  const hasSelection = useSession((s) => Boolean(activeId && s.selections[activeId]))
  const { snap } = usePointSettings()

  const run = (fn: () => void) => () => {
    usePalette.setState({ open: false })
    fn()
  }
  const display = (changes: Parameters<typeof updateDisplay>[1]) =>
    run(() => activeId && updateDisplay(activeId, changes))

  return (
    <CommandDialog
      open={open}
      onOpenChange={(o) => usePalette.setState({ open: o })}
      title="Command palette"
      description="Search for a command"
    >
      <Command>
        <CommandInput placeholder="Type a command…" />
        <CommandList>
          <CommandEmpty>No matching command.</CommandEmpty>
          <CommandGroup heading="File">
            <CommandItem onSelect={run(() => showOpenDialog())}>
              Open file or project…<CommandShortcut>{mod}O</CommandShortcut>
            </CommandItem>
            {order.length > 0 && (
              <>
                <CommandItem onSelect={run(() => void saveProject())}>
                  Save project<CommandShortcut>{mod}S</CommandShortcut>
                </CommandItem>
                <CommandItem onSelect={run(() => void saveProject(true))}>
                  Save project as…<CommandShortcut>{mod}Shift+S</CommandShortcut>
                </CommandItem>
              </>
            )}
            {(project || order.length > 0) && (
              <CommandItem onSelect={run(() => void closeProject())}>
                Close all{project ? ` (project ${project.name})` : ''}
              </CommandItem>
            )}
            {recent.map((path) => (
              <CommandItem
                key={path}
                value={`open recent ${path}`}
                onSelect={run(() => void openPath(path))}
              >
                Open recent: {basename(path)}
              </CommandItem>
            ))}
          </CommandGroup>
          {order.length > 0 && (
            <CommandGroup heading="Documents">
              {order.map((id) => (
                <CommandItem
                  key={id}
                  value={`switch to ${documents[id]?.name} ${id}`}
                  onSelect={run(() => setActiveDocument(id))}
                >
                  Switch to {documents[id]?.name}
                </CommandItem>
              ))}
              {activeId && (
                <CommandItem onSelect={run(() => void closeDocument(activeId))}>
                  Close {documents[activeId]?.name}
                </CommandItem>
              )}
            </CommandGroup>
          )}
          {activeId && (
            <CommandGroup heading="View">
              <CommandItem onSelect={run(() => setTool('select'))}>
                Tool: select / inspect<CommandShortcut>V</CommandShortcut>
              </CommandItem>
              <CommandItem onSelect={run(() => setTool('pan'))}>
                Tool: pan<CommandShortcut>H</CommandShortcut>
              </CommandItem>
              <CommandItem onSelect={run(() => setTool('zoom'))}>
                Tool: zoom box<CommandShortcut>Z</CommandShortcut>
              </CommandItem>
              <CommandItem onSelect={run(() => resetZoom(activeId))}>
                Reset zoom<CommandShortcut>0</CommandShortcut>
              </CommandItem>
              {(['png', 'svg'] as const).flatMap((format) =>
                (['light', 'dark'] as const).map((theme) => (
                  <CommandItem
                    key={`${format}-${theme}`}
                    onSelect={run(() => void exportChromatogram(activeId, format, theme))}
                  >
                    Save figure as {format.toUpperCase()} ({theme})
                  </CommandItem>
                )),
              )}
              {COLORMAPS.map((c) => (
                <CommandItem key={c} onSelect={display({ colormap: c })}>
                  Colour map: {c}
                </CommandItem>
              ))}
              {SCALINGS.map((s) => (
                <CommandItem key={s} onSelect={display({ scaling: s })}>
                  Scaling: {SCALING_LABELS[s]}
                </CommandItem>
              ))}
              {INTERPOLATIONS.map((i) => (
                <CommandItem key={i} onSelect={display({ interpolation: i })}>
                  Interpolation: {INTERPOLATION_LABELS[i]}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {activeDoc?.kind === 'ms' && (
            <CommandGroup heading="GCxGC-MS">
              <CommandItem
                onSelect={run(
                  () =>
                    void detectPeaks(
                      activeDoc.id,
                      useSession.getState().settings.ms?.peak_snr ?? 10,
                    ),
                )}
              >
                Detect &amp; identify peaks
              </CommandItem>
              {activeDoc.has_peaks && (
                <CommandItem onSelect={run(() => void downloadPeaks(activeDoc))}>
                  Export peak table (CSV)
                </CommandItem>
              )}
              {hasSelection && (
                <>
                  <CommandItem onSelect={run(() => void copySpectrum(activeDoc.id))}>
                    Copy spectrum as text
                  </CommandItem>
                  <CommandItem onSelect={run(() => void downloadSpectrum(activeDoc.id))}>
                    Save spectrum as MSP
                  </CommandItem>
                </>
              )}
              <CommandItem onSelect={run(() => setPointSettings({ snap: !snap }))}>
                {snap ? 'Turn off snap to apex' : 'Snap to apex'}
              </CommandItem>
              <CommandItem onSelect={run(() => showPanel('libraries'))}>
                Manage libraries…
              </CommandItem>
            </CommandGroup>
          )}
          {Object.values(libraries).length > 0 && (
            <CommandGroup heading="Library">
              {Object.values(libraries).map((lib) => (
                <CommandItem
                  key={lib.id}
                  value={`library ${lib.name} ${lib.id}`}
                  disabled={lib.status !== 'ready'}
                  onSelect={run(() => void setLibraryActive(lib.id, true))}
                >
                  Use library: {lib.name}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          <CommandGroup heading="App">
            <CommandItem onSelect={run(() => setTheme('light'))}>Theme: light</CommandItem>
            <CommandItem onSelect={run(() => setTheme('dark'))}>Theme: dark</CommandItem>
            <CommandItem onSelect={run(() => setTheme('system'))}>Theme: system</CommandItem>
            <CommandItem onSelect={run(() => resetLayout())}>Reset layout</CommandItem>
            <CommandItem onSelect={run(() => openSettings())}>Settings…</CommandItem>
            <CommandItem onSelect={run(() => void openLogFolder())}>Open log folder</CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  )
}
