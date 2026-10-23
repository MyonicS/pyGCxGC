/**
 * Built-in file browser (spec SHELL-5, browser/notebook mode), backed by
 * `/api/fs`. Selecting a file probes it: FID files ask for the modulation
 * time, MS files may leave it empty (estimated from the TIC).
 */

import { ArrowUp, File, FileChartLine, Folder, Home, Loader2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { FileProbe, FsListing } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { cn, dirname, formatBytes } from '@/lib/utils'
import { api, updateSettings, useSession } from '@/store/session'

import {
  hideOpenDialog,
  isSupported,
  openPath,
  rememberModulationTime,
  useOpenDialog,
} from './openFile'

export function OpenDialog() {
  const { open, path: preselect } = useOpenDialog()
  return (
    <Dialog open={open} onOpenChange={(o) => !o && hideOpenDialog()}>
      {open && <OpenDialogBody preselect={preselect} />}
    </Dialog>
  )
}

function OpenDialogBody({ preselect }: { preselect: string | null }) {
  const settings = useSession((s) => s.settings)
  const [folder, setFolder] = useState<string | null>(null)
  const [listing, setListing] = useState<FsListing | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [pathText, setPathText] = useState('')
  const [allFiles, setAllFiles] = useState(false)
  const [selected, setSelected] = useState<string | null>(preselect)
  const [probeState, setProbe] = useState<FileProbe | null>(null)
  const [modulation, setModulation] = useState('')
  const [remember, setRemember] = useState(true)
  const [opening, setOpening] = useState(false)

  /** A double-clicked file, opened as soon as its probe allows. */
  const pendingOpen = useRef<string | null>(null)

  const showListing = (result: FsListing) => {
    setListing(result)
    setFolder(result.path)
    setPathText(result.path)
    setListError(null)
  }
  const browse = (path?: string) =>
    api()
      .listDir(path)
      .then(showListing)
      .catch((err: unknown) => setListError(err instanceof Error ? err.message : String(err)))

  // start in the folder of the preselected file, the last folder or home
  useEffect(() => {
    const start = preselect ? dirname(preselect) : (settings.ui?.last_folder ?? undefined)
    let cancelled = false
    api()
      .listDir(start)
      .catch(() => api().listDir())
      .then((result) => !cancelled && showListing(result))
      .catch((err: unknown) => !cancelled && setListError(String(err)))
    return () => {
      cancelled = true
    }
    // only when the dialog opens
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // the probe of the selected file (a stale one is ignored)
  const probe = probeState && probeState.path === selected ? probeState : null

  const entries = useMemo(
    () => (listing?.entries ?? []).filter((e) => e.is_dir || allFiles || isSupported(e.name)),
    [listing, allFiles],
  )

  const valid = (p: FileProbe | null, text: string) =>
    Boolean(p && !p.error) && (text.trim() === '' ? p?.kind !== 'fid' : Number(text) > 0)
  const canOpen = Boolean(selected) && valid(probe, modulation) && !opening

  const doOpen = async (p: FileProbe | null = probe, text = modulation) => {
    if (!p || !valid(p, text) || opening) return
    setOpening(true)
    if (folder) void updateSettings({ ui: { last_folder: folder } })
    const seconds = Number(text)
    const params = text.trim() ? { modulation_time: seconds } : {}
    if (p.kind === 'fid' && remember && seconds > 0) rememberModulationTime(seconds)
    hideOpenDialog()
    await openPath(p.path, params)
  }

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    api()
      .probe(selected)
      .then((p) => {
        if (cancelled) return
        const fallback = useSession.getState().settings.fid?.modulation_time
        const text = p.kind === 'fid' && fallback ? String(fallback) : ''
        setProbe(p)
        setModulation(text)
        if (pendingOpen.current === p.path) {
          pendingOpen.current = null
          void doOpen(p, text)
        }
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
    // doOpen only reads state that is passed in or set before
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  return (
    <DialogContent
      className="flex max-h-[85vh] flex-col gap-3 sm:max-w-2xl"
      data-testid="open-dialog"
    >
      <DialogHeader>
        <DialogTitle>Open chromatogram</DialogTitle>
        <DialogDescription>
          OpenChrom CSV (FID or MS), ANDI-MS .cdf or JCAMP-DX files.
        </DialogDescription>
      </DialogHeader>
      <form
        className="flex gap-1"
        onSubmit={(e) => {
          e.preventDefault()
          void browse(pathText)
        }}
      >
        <Button
          type="button"
          variant="outline"
          size="icon-sm"
          aria-label="Home folder"
          onClick={() => void browse()}
        >
          <Home />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="icon-sm"
          aria-label="Parent folder"
          disabled={!listing?.parent}
          onClick={() => listing?.parent && void browse(listing.parent)}
        >
          <ArrowUp />
        </Button>
        <Input
          value={pathText}
          onChange={(e) => setPathText(e.target.value)}
          className="h-7 font-mono text-xs"
          aria-label="Folder"
        />
      </form>
      <ScrollArea className="h-80 rounded-md border">
        <div className="flex flex-col p-1" role="listbox" aria-label="Files">
          {listError && <p className="p-2 text-sm text-destructive">{listError}</p>}
          {entries.map((e) => {
            const isSelected = selected === e.path
            return (
              <button
                key={e.path}
                type="button"
                role="option"
                aria-selected={isSelected}
                className={cn(
                  'flex items-center gap-2 rounded-sm px-2 py-1 text-left text-sm hover:bg-accent',
                  isSelected && 'bg-accent',
                  !e.is_dir && !isSupported(e.name) && 'text-muted-foreground',
                )}
                onClick={() => (e.is_dir ? void browse(e.path) : setSelected(e.path))}
                onDoubleClick={() => {
                  if (!e.is_dir) {
                    pendingOpen.current = e.path
                    if (probe?.path === e.path) void doOpen()
                    else setSelected(e.path)
                  }
                }}
              >
                {e.is_dir ? (
                  <Folder className="size-4 shrink-0 text-primary" />
                ) : isSupported(e.name) ? (
                  <FileChartLine className="size-4 shrink-0" />
                ) : (
                  <File className="size-4 shrink-0" />
                )}
                <span className="min-w-0 flex-1 truncate">{e.name}</span>
                {!e.is_dir && (
                  <span className="shrink-0 font-mono text-xs text-muted-foreground tabular">
                    {formatBytes(e.size)}
                  </span>
                )}
              </button>
            )
          })}
          {listing && entries.length === 0 && (
            <p className="p-2 text-sm text-muted-foreground">No chromatogram files here.</p>
          )}
        </div>
      </ScrollArea>
      <label className="flex items-center gap-2 self-start text-xs text-muted-foreground">
        <Switch checked={allFiles} onCheckedChange={setAllFiles} size="sm" />
        Show all files
      </label>

      <div className="flex min-h-16 flex-col gap-2 rounded-md bg-muted/60 p-3">
        {!selected && <p className="text-sm text-muted-foreground">Select a file.</p>}
        {selected && (
          <div className="flex items-center gap-2 text-sm">
            <span className="min-w-0 flex-1 truncate font-medium">{selected}</span>
            {probe?.kind && <Badge variant="secondary">{probe.kind.toUpperCase()}</Badge>}
          </div>
        )}
        {probe?.error && <p className="text-xs text-destructive">{probe.error}</p>}
        {probe && !probe.error && (
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="open-mod" className="text-xs">
                Modulation time (s){probe.kind === 'fid' ? '' : ' – empty: estimate'}
              </Label>
              <Input
                id="open-mod"
                value={modulation}
                onChange={(e) => setModulation(e.target.value)}
                inputMode="decimal"
                placeholder={probe.kind === 'fid' ? 'required' : 'estimate'}
                className="h-7 w-32 font-mono text-xs tabular"
                aria-invalid={modulation.trim() !== '' && !(Number(modulation) > 0)}
                autoFocus={probe.kind === 'fid'}
                onKeyDown={(e) => e.key === 'Enter' && void doOpen()}
              />
            </div>
            {probe.kind === 'fid' && (
              <label className="flex items-center gap-2 pb-1 text-xs text-muted-foreground">
                <Switch checked={remember} onCheckedChange={setRemember} size="sm" />
                Use for the next FID files
              </label>
            )}
          </div>
        )}
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={hideOpenDialog}>
          Cancel
        </Button>
        <Button disabled={!canOpen} onClick={() => void doOpen()} data-testid="open-confirm">
          {opening && <Loader2 className="animate-spin" />}
          Open
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}
