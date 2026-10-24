/**
 * Libraries panel (spec LIB-1, LIB-3, LIB-5): the loaded libraries with
 * source, size, licence note and cache status; *Add…* (a NIST MAINLIB folder
 * or an MSP/zip/npz file), *Download MoNA GC-MS* (a job) and remove. Several
 * libraries can be searched at once. The search settings and the online
 * structure lookup are at the bottom.
 */

import {
  CloudDownload,
  Database,
  FolderPlus,
  HardDrive,
  Loader2,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useState } from 'react'

import type { LibraryInfo, SearchSettings } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { pickPath } from '@/shell/PathDialog'
import { addLibrary, removeLibrary, toggleLibrary } from '@/store/ms'
import { cancelJob, updateSettings, useActiveDocument, useSession } from '@/store/session'

const SOURCE_LABEL: Record<LibraryInfo['source'], string> = {
  nist: 'NIST',
  mona: 'MoNA',
  msp: 'MSP',
  npz: 'Cache',
}

async function addFromFile() {
  const path = await pickPath({
    title: 'Add spectral library',
    description:
      'A NIST MS Search MAINLIB (or MSSEARCH) folder, or an MSP, zip or npz file. Libraries are converted once and cached.',
    mode: 'any',
    extensions: ['.msp', '.zip', '.npz'],
    confirm: 'Add',
  })
  if (path) await addLibrary(path)
}

export function LibrariesPanel() {
  const libraries = useSession((s) => s.libraries)
  const list = Object.values(libraries)
  const jobs = useSession((s) => s.jobs)
  const loading = Object.values(jobs).filter(
    (j) => j.kind === 'library' && (j.status === 'queued' || j.status === 'running'),
  )
  const hasMona = list.some((l) => l.source === 'mona' && l.status !== 'failed')

  return (
    <div className="flex h-full flex-col overflow-auto bg-panel" data-testid="libraries-panel">
      <div className="flex min-h-9 shrink-0 flex-wrap items-center gap-1.5 border-b px-2 py-1">
        <Button
          size="sm"
          variant="outline"
          onClick={() => void addFromFile()}
          data-testid="add-library"
        >
          <FolderPlus /> Add…
        </Button>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              size="sm"
              variant="ghost"
              disabled={hasMona}
              onClick={() => void addLibrary('mona')}
            >
              <CloudDownload /> Download MoNA GC-MS
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            The open MassBank of North America GC-MS library (about 9 MB, needs the internet)
          </TooltipContent>
        </Tooltip>
      </div>
      {loading.map((j) => (
        <div key={j.id} className="flex items-center gap-2 border-b px-2 py-1.5 text-xs">
          <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
          <span className="truncate">{j.message || j.label}</span>
          <Progress value={j.progress * 100} className="ml-auto h-1.5 w-24 shrink-0" />
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label="Cancel"
            onClick={() => void cancelJob(j.id)}
          >
            <X />
          </Button>
        </div>
      ))}
      {list.length === 0 && loading.length === 0 ? (
        <div className="flex flex-col items-center gap-2 p-6 text-center text-xs text-muted-foreground">
          <Database className="size-5 opacity-60" />
          <p className="max-w-64">
            Add a spectral library to identify spectra: your NIST MS Search folder, an MSP file, or
            the open MoNA GC-MS library.
          </p>
        </div>
      ) : (
        <ul className="divide-y border-b">
          {list.map((lib) => (
            <LibraryRow key={lib.id} lib={lib} />
          ))}
        </ul>
      )}
      <SearchSettingsForm />
    </div>
  )
}

function LibraryRow({ lib }: { lib: LibraryInfo }) {
  return (
    <li className="flex items-start gap-2 px-2 py-2 text-xs" data-testid="library-row">
      <Switch
        size="sm"
        className="mt-0.5"
        checked={lib.active}
        disabled={lib.status !== 'ready'}
        onCheckedChange={(v) => void toggleLibrary(lib.id, v)}
        aria-label={`Search ${lib.name}`}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-1.5">
          <span className="truncate font-medium" title={lib.path}>
            {lib.name}
          </span>
          <Badge variant="outline" className="h-4 px-1.5 text-[10px]">
            {SOURCE_LABEL[lib.source]}
          </Badge>
        </div>
        <div className="flex flex-wrap gap-x-2 text-muted-foreground">
          {lib.status === 'ready' && <span>{lib.n_spectra?.toLocaleString()} spectra</span>}
          {lib.status === 'loading' && <span>Loading…</span>}
          {lib.cache_file && (
            <span className="flex items-center gap-1" title={lib.cache_file}>
              <HardDrive className="size-3" />
              {lib.cached ? 'cached' : 'not cached yet'}
            </span>
          )}
        </div>
        {lib.licence && <p className="text-[11px] text-muted-foreground">{lib.licence}</p>}
        {lib.status === 'failed' && (
          <p className="flex gap-1 text-destructive">
            <TriangleAlert className="size-3.5 shrink-0" />
            {lib.error}
          </p>
        )}
      </div>
      <Button
        size="icon-xs"
        variant="ghost"
        aria-label={`Remove ${lib.name}`}
        onClick={() => void removeLibrary(lib.id)}
      >
        <Trash2 />
      </Button>
    </li>
  )
}

type Draft = Record<keyof SearchSettings, string>

const FIELDS: { key: keyof SearchSettings; label: string; hint?: string; int?: boolean }[] = [
  { key: 'mz_min', label: 'm/z from', int: true },
  { key: 'mz_max', label: 'm/z to', int: true },
  { key: 'mz_power', label: 'm/z weight', hint: 'exponent' },
  { key: 'int_power', label: 'Intensity weight', hint: 'exponent' },
  { key: 'n_hits', label: 'Hits', int: true },
  { key: 'min_match', label: 'Min match' },
]

/** Search settings (spec LIB-5); a change re-searches the current spectrum. */
function SearchSettingsForm() {
  const search = useSession((s) => s.settings.search)
  const online = useSession((s) => s.settings.structures?.online ?? 'ask')
  const doc = useActiveDocument()
  const [draft, setDraft] = useState<Partial<Draft>>({})
  const [lo, hi] = doc?.mass_range ?? [null, null]

  const shown = (key: keyof SearchSettings) => {
    if (draft[key] !== undefined) return draft[key]
    const v = search?.[key]
    return v === null || v === undefined ? '' : String(v)
  }
  const commit = (key: keyof SearchSettings, int?: boolean) => {
    const text = draft[key]
    if (text === undefined) return
    setDraft((d) => ({ ...d, [key]: undefined }))
    const range = key === 'mz_min' || key === 'mz_max'
    if (text.trim() === '' && range) {
      void updateSettings({ search: { [key]: null } })
      return
    }
    const v = Number(text)
    if (!Number.isFinite(v) || v < 0 || (int && !Number.isInteger(v))) return
    if (key === 'n_hits' && (v < 1 || v > 100)) return
    if (v !== search?.[key]) void updateSettings({ search: { [key]: v } })
  }

  return (
    <div className="flex flex-col gap-2 p-2 text-xs">
      <h3 className="font-medium text-muted-foreground">Search settings</h3>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
        {FIELDS.map((f) => (
          <div key={f.key} className="flex items-center justify-between gap-2">
            <Label htmlFor={`search-${f.key}`} className="text-xs font-normal">
              {f.label}
            </Label>
            <Input
              id={`search-${f.key}`}
              value={shown(f.key)}
              placeholder={
                f.key === 'mz_min'
                  ? String(lo ?? 'data')
                  : f.key === 'mz_max'
                    ? String(hi ?? 'data')
                    : ''
              }
              onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
              onBlur={() => commit(f.key, f.int)}
              onKeyDown={(e) => e.key === 'Enter' && commit(f.key, f.int)}
              inputMode="decimal"
              className="h-7 w-20 px-1.5 text-right font-mono text-xs tabular"
            />
          </div>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        The mass range defaults to the acquired range; library peaks outside it are ignored.
      </p>
      <div className="flex items-center justify-between gap-2">
        <Label className="text-xs font-normal">Structures from PubChem</Label>
        <Select
          value={online}
          onValueChange={(v) => void updateSettings({ structures: { online: v as 'ask' } })}
        >
          <SelectTrigger
            size="sm"
            className="h-7 w-24 text-xs"
            aria-label="Online structure lookup"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ask" className="text-xs">
              Ask
            </SelectItem>
            <SelectItem value="on" className="text-xs">
              On
            </SelectItem>
            <SelectItem value="off" className="text-xs">
              Off
            </SelectItem>
          </SelectContent>
        </Select>
      </div>
    </div>
  )
}
