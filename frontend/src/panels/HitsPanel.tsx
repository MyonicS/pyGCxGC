/**
 * Hits panel (spec MS-6): the library hits of the selection, sortable.
 * Clicking a hit shows it in the Match panel; the top hit is selected by
 * default. *Assign to peak* makes the hit the selected peak's identity (MS-9).
 */

import type { ColDef, RowClassParams, RowClickedEvent } from 'ag-grid-community'
import { BookPlus, Loader2, Tag } from 'lucide-react'
import { useMemo } from 'react'

import type { DocumentInfo, HitInfo } from '@/api/types'
import { DataGrid } from '@/components/DataGrid'
import { Button } from '@/components/ui/button'
import { showPanel } from '@/shell/Workspace'
import { editPeak, selectHit, useSelectedHit, useSelectedPeakId } from '@/store/ms'
import { useActiveDocument, useSession } from '@/store/session'

const num = 'font-mono tabular'

const columns: ColDef<HitInfo>[] = [
  { field: 'rank', headerName: '#', width: 52, type: 'rightAligned', cellClass: num },
  { field: 'name', headerName: 'Name', flex: 1, minWidth: 140 },
  {
    field: 'match',
    headerName: 'Match',
    width: 74,
    type: 'rightAligned',
    cellClass: num,
    valueFormatter: (p) => String(Math.round(p.value as number)),
  },
  {
    field: 'reverse_match',
    headerName: 'R.match',
    width: 80,
    type: 'rightAligned',
    cellClass: num,
    valueFormatter: (p) => String(Math.round(p.value as number)),
  },
  { field: 'formula', headerName: 'Formula', width: 96 },
  { field: 'mw', headerName: 'MW', width: 64, type: 'rightAligned', cellClass: num },
  { field: 'cas', headerName: 'CAS', width: 96, cellClass: num },
  {
    field: 'nist_no',
    headerName: 'NIST#',
    width: 78,
    type: 'rightAligned',
    cellClass: num,
    valueFormatter: (p) => ((p.value as number) ? String(p.value) : ''),
  },
  { field: 'library', headerName: 'Library', width: 110 },
]

export function HitsPanel() {
  const doc = useActiveDocument()
  if (!doc || doc.kind !== 'ms') {
    return (
      <div className="flex h-full items-center justify-center bg-panel p-4 text-xs text-muted-foreground">
        {doc ? 'Library hits need a GCxGC-MS document' : 'No document'}
      </div>
    )
  }
  return <HitsView doc={doc} />
}

function HitsView({ doc }: { doc: DocumentInfo }) {
  const sel = useSession((s) => s.selections[doc.id] ?? null)
  const hasLibrary = useSession((s) =>
    Object.values(s.libraries).some((l) => l.active && l.status === 'ready'),
  )
  const loading = useSession((s) => Object.values(s.libraries).some((l) => l.status === 'loading'))
  const selected = useSelectedHit(doc.id)
  const peakId = useSelectedPeakId(doc.id)
  const hits = sel?.hits ?? null
  const rowClassRules = useMemo(
    () => ({
      'row-current': (p: RowClassParams<HitInfo>) =>
        Boolean(selected && p.data && p.data.rank === selected.rank),
    }),
    [selected],
  )

  let message: React.ReactNode = null
  if (!sel) message = 'Click the chromatogram to search a spectrum'
  else if (!hasLibrary)
    message = loading ? (
      <span className="flex items-center gap-1.5">
        <Loader2 className="size-3.5 animate-spin" /> Loading library…
      </span>
    ) : (
      <span className="flex flex-col items-center gap-2">
        No active library
        <Button size="sm" variant="outline" onClick={() => showPanel('libraries')}>
          <BookPlus /> Libraries
        </Button>
      </span>
    )
  else if (hits === null)
    message = (
      <span className="flex items-center gap-1.5">
        <Loader2 className="size-3.5 animate-spin" /> Searching…
      </span>
    )
  else if (hits.length === 0) message = 'No hits (empty spectrum?)'

  return (
    <div className="flex h-full flex-col bg-panel" data-testid="hits-panel">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-2 text-xs text-muted-foreground">
        <span className="truncate">
          {hits?.length
            ? `${hits.length} hits · ${sel?.libraries.join(', ')}`
            : sel?.libraries.length
              ? sel.libraries.join(', ')
              : 'Library hits'}
        </span>
        {peakId !== undefined && selected && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={() =>
              void editPeak(doc.id, { peak_id: peakId, hit_rank: selected.rank }, 'Assign hit')
            }
            data-testid="assign-hit"
          >
            <Tag /> Assign to peak {peakId}
          </Button>
        )}
      </div>
      <div className="relative min-h-0 flex-1">
        {message ? (
          <div className="absolute inset-0 flex items-center justify-center p-4 text-center text-xs text-muted-foreground">
            {message}
          </div>
        ) : (
          <DataGrid<HitInfo>
            rowData={hits ?? []}
            columnDefs={columns}
            getRowId={(p) => String(p.data.rank)}
            rowClassRules={rowClassRules}
            onRowClicked={(e: RowClickedEvent<HitInfo>) => e.data && selectHit(doc.id, e.data.rank)}
            defaultColDef={{ sortable: true, resizable: true }}
          />
        )}
      </div>
    </div>
  )
}
