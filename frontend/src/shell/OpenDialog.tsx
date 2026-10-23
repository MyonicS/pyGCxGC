/**
 * Open dialog (spec SHELL-5, browser/notebook mode). Selecting a file probes
 * it: FID files ask for the modulation time, MS files may leave it empty
 * (estimated from the TIC).
 */

import { Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { FileProbe } from '@/api/types'
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
import { Switch } from '@/components/ui/switch'
import { dirname } from '@/lib/utils'
import { api, updateSettings, useSession } from '@/store/session'

import { FileBrowser } from './FileBrowser'
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
  const [selected, setSelected] = useState<string | null>(preselect)
  const [probeState, setProbe] = useState<FileProbe | null>(null)
  const [modulation, setModulation] = useState('')
  const [remember, setRemember] = useState(true)
  const [opening, setOpening] = useState(false)

  /** A double-clicked file, opened as soon as its probe allows. */
  const pendingOpen = useRef<string | null>(null)

  // the probe of the selected file (a stale one is ignored)
  const probe = probeState && probeState.path === selected ? probeState : null

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
      <FileBrowser
        start={preselect ? dirname(preselect) : (settings.ui?.last_folder ?? undefined)}
        isRelevant={isSupported}
        emptyText="No chromatogram files here."
        selected={selected}
        onSelect={(e) => setSelected(e.path)}
        onActivate={(e) => {
          pendingOpen.current = e.path
          if (probe?.path === e.path) void doOpen()
          else setSelected(e.path)
        }}
        onFolderChange={setFolder}
      />

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
