/**
 * Peaks panel (spec MS-8, MS-9): *Detect & identify* with a peak S/N (a job
 * with progress and cancel), the peak table (sortable, filtered by name and
 * minimum match), CSV export, and editing: rename in place, assign the
 * selected hit (Hits panel), delete. Edits are marked `assigned_by = user`.
 * A table identified with other libraries or processing is marked stale
 * with a *Re-identify* button (LIB-2, BASE-4); it is never re-identified
 * automatically.
 */

import type {
  CellValueChangedEvent,
  ColDef,
  RowClassParams,
  RowClickedEvent,
} from 'ag-grid-community'
import { AlertTriangle, Download, Loader2, RefreshCw, ScanSearch, Trash2, X } from 'lucide-react'
import { useMemo, useState } from 'react'

import type { DocumentInfo, PeakRow } from '@/api/types'
import { DataGrid } from '@/components/DataGrid'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { formatValue } from '@/lib/ticks'
import {
  detectPeaks,
  downloadPeaks,
  editPeak,
  reidentifyPeaks,
  selectPeak,
  useSelectedPeakId,
} from '@/store/ms'
import { cancelJob, useActiveDocument, useSession } from '@/store/session'

const num = 'font-mono tabular'

const columns: ColDef<PeakRow>[] = [
  { field: 'peak_id', headerName: '#', width: 58, type: 'rightAligned', cellClass: num },
  {
    field: 'rt1_min',
    headerName: 'RT1 (min)',
    width: 88,
    type: 'rightAligned',
    cellClass: num,
    valueFormatter: (p) => (p.value as number).toFixed(2),
  },
  {
    field: 'rt2_s',
    headerName: 'RT2 (s)',
    width: 76,
    type: 'rightAligned',
    cellClass: num,
    valueFormatter: (p) => (p.value as number).toFixed(2),
  },
  {
    field: 'name',
    headerName: 'Name',
    flex: 1,
    minWidth: 140,
    editable: true,
    cellClass: (p) => (p.data?.assigned_by === 'user' ? 'font-medium' : ''),
  },
  {
    field: 'match',
    headerName: 'Match',
    width: 72,
    type: 'rightAligned',
    cellClass: num,
    valueFormatter: (p) => ((p.value as number) ? String(Math.round(p.value as number)) : ''),
  },
  {
    field: 'reverse_match',
    headerName: 'R.match',
    width: 78,
    type: 'rightAligned',
    cellClass: num,
    valueFormatter: (p) => ((p.value as number) ? String(Math.round(p.value as number)) : ''),
  },
  { field: 'formula', headerName: 'Formula', width: 90 },
  { field: 'cas', headerName: 'CAS', width: 92, cellClass: num },
  { field: 'library', headerName: 'Library', width: 100 },
  {
    field: 'height',
    headerName: 'Height',
    width: 84,
    type: 'rightAligned',
    cellClass: num,
    valueFormatter: (p) => formatValue(p.value as number),
  },
  {
    field: 'volume',
    headerName: 'Volume',
    width: 88,
    type: 'rightAligned',
    cellClass: num,
    valueFormatter: (p) => formatValue(p.value as number),
  },
  // identity from the library or set by the user (MS-9)
  { field: 'assigned_by', headerName: 'By', width: 70 },
]

export function PeaksPanel() {
  const doc = useActiveDocument()
  if (!doc || doc.kind !== 'ms') {
    return (
      <div className="flex h-full items-center justify-center bg-panel p-4 text-xs text-muted-foreground">
        {doc ? 'Peak detection needs a GCxGC-MS document' : 'No document'}
      </div>
    )
  }
  return <PeaksView doc={doc} />
}

function PeaksView({ doc }: { doc: DocumentInfo }) {
  const table = useSession((s) => s.peaks[doc.id] ?? null)
  const job = useSession((s) =>
    Object.values(s.jobs).find(
      (j) =>
        j.document_id === doc.id &&
        j.kind === 'peaks' &&
        (j.status === 'queued' || j.status === 'running'),
    ),
  )
  const savedSnr = useSession((s) => s.settings.ms?.peak_snr ?? 10)
  const [snr, setSnr] = useState<string | null>(null)
  const [nameFilter, setNameFilter] = useState('')
  const [minMatch, setMinMatch] = useState('')
  const selectedId = useSelectedPeakId(doc.id)

  const rows = useMemo(() => (table?.rows ?? []) as unknown as PeakRow[], [table])
  const filtered = useMemo(() => {
    const text = nameFilter.trim().toLowerCase()
    const min = Number(minMatch) || 0
    return rows.filter(
      (r) => (!text || r.name.toLowerCase().includes(text)) && (r.match ?? 0) >= min,
    )
  }, [rows, nameFilter, minMatch])
  const rowClassRules = useMemo(
    () => ({
      'row-current': (p: RowClassParams<PeakRow>) => p.data?.peak_id === selectedId,
    }),
    [selectedId],
  )

  const snrValue = Number(snr ?? savedSnr)
  const snrValid = Number.isFinite(snrValue) && snrValue > 0

  return (
    <div className="flex h-full flex-col bg-panel" data-testid="peaks-panel">
      <div className="flex min-h-9 shrink-0 flex-wrap items-center gap-1.5 border-b px-2 py-1">
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          S/N
          <Input
            value={snr ?? String(savedSnr)}
            onChange={(e) => setSnr(e.target.value)}
            inputMode="decimal"
            aria-label="Peak S/N"
            aria-invalid={!snrValid}
            className="h-7 w-14 px-1.5 text-center font-mono text-xs tabular"
          />
        </label>
        <Button
          size="sm"
          disabled={!snrValid || Boolean(job)}
          onClick={() => void detectPeaks(doc.id, snrValue)}
          data-testid="detect-peaks"
        >
          {job ? <Loader2 className="animate-spin" /> : <ScanSearch />}
          Detect &amp; identify
        </Button>
        {job && (
          <div className="flex min-w-28 flex-1 items-center gap-1.5" data-testid="peaks-progress">
            <Progress value={job.progress * 100} className="h-1.5 max-w-40" />
            <span className="truncate text-[11px] text-muted-foreground">{job.message}</span>
            <Button
              size="icon-xs"
              variant="ghost"
              aria-label="Cancel"
              onClick={() => void cancelJob(job.id)}
            >
              <X />
            </Button>
          </div>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <Input
            value={nameFilter}
            onChange={(e) => setNameFilter(e.target.value)}
            placeholder="Filter name"
            aria-label="Filter by name"
            className="h-7 w-32 text-xs"
          />
          <Input
            value={minMatch}
            onChange={(e) => setMinMatch(e.target.value)}
            placeholder="Min match"
            inputMode="numeric"
            aria-label="Minimum match"
            className="h-7 w-20 text-xs"
          />
          {selectedId !== undefined && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label={`Delete peak ${selectedId}`}
                  onClick={() =>
                    void editPeak(doc.id, { peak_id: selectedId, delete: true }, 'Delete peak')
                  }
                  data-testid="delete-peak"
                >
                  <Trash2 />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Delete peak {selectedId}</TooltipContent>
            </Tooltip>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label="Export peak table"
                disabled={!table}
                onClick={() => void downloadPeaks(doc)}
              >
                <Download />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Export the peak table (CSV)</TooltipContent>
          </Tooltip>
        </div>
      </div>
      {table?.stale && (
        <div
          className="flex shrink-0 items-center gap-2 border-b bg-warning/10 px-2 py-1 text-xs"
          data-testid="peaks-stale"
        >
          <AlertTriangle className="size-3.5 shrink-0 text-warning" />
          <span className="truncate">
            Stale: {table.stale_reason ?? 'the settings changed'}
            {table.libraries.length ? ` (identified with ${table.libraries.join(', ')})` : ''}
          </span>
          <Button
            size="xs"
            variant="outline"
            className="ml-auto"
            disabled={Boolean(job)}
            onClick={() => void reidentifyPeaks(doc.id)}
          >
            <RefreshCw /> Re-identify
          </Button>
        </div>
      )}
      <div className="relative min-h-0 flex-1">
        {!table ? (
          <div className="absolute inset-0 flex items-center justify-center p-4 text-center text-xs text-muted-foreground">
            Detect peaks to build a peak table
          </div>
        ) : (
          <DataGrid<PeakRow>
            rowData={filtered}
            columnDefs={columns}
            getRowId={(p) => String(p.data.peak_id)}
            rowClassRules={rowClassRules}
            defaultColDef={{ sortable: true, resizable: true }}
            singleClickEdit={false}
            stopEditingWhenCellsLoseFocus
            onRowClicked={(e: RowClickedEvent<PeakRow>) =>
              e.data && void selectPeak(doc.id, e.data)
            }
            onCellValueChanged={(e: CellValueChangedEvent<PeakRow>) => {
              if (e.colDef.field === 'name' && e.data && e.newValue !== e.oldValue) {
                void editPeak(doc.id, { peak_id: e.data.peak_id, name: String(e.newValue ?? '') })
              }
            }}
          />
        )}
      </div>
      {table && (
        <div className="shrink-0 border-t px-2 py-0.5 text-[11px] text-muted-foreground">
          {filtered.length === rows.length
            ? `${rows.length} peaks`
            : `${filtered.length} of ${rows.length} peaks`}
          {table.snr ? ` · S/N ${table.snr}` : ''}
          {table.baseline ? ` · ${table.baseline}` : ''}
        </div>
      )}
    </div>
  )
}
