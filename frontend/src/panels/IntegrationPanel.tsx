/**
 * Integration panel (spec INT-1): one row per mask with its volume (on the
 * processed signal, `integrate_2D` as `integrate_masks`), % of the total and
 * pixel count, plus the unassigned rest. Warns about overlapping masks
 * (MASK-7). CSV export (INT-2) and "Copy as Python" (INT-5).
 */

import type { ColDef, ICellRendererParams, RowClickedEvent } from 'ag-grid-community'
import { AlertTriangle, ClipboardCopy, Download, Files } from 'lucide-react'
import { useMemo } from 'react'
import { toast } from 'sonner'

import type { DocumentInfo, IntegrationRow } from '@/api/types'
import { DataGrid } from '@/components/DataGrid'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { notifyError, notifyWarnings } from '@/lib/notify'
import { maskSetFilename, pythonSnippet } from '@/lib/pythonSnippet'
import { formatValue } from '@/lib/ticks'
import { downloadExport, saveFile } from '@/store/masks'
import { api, useActiveDocument, useSession, waitForJob } from '@/store/session'
import { pickPaths } from '@/shell/PathDialog'
import { setActiveMask } from '@/store/view'

interface Row extends IntegrationRow {
  color: string | null
}

const columns: ColDef<Row>[] = [
  {
    field: 'name',
    headerName: 'Mask',
    flex: 1,
    minWidth: 110,
    cellRenderer: (p: ICellRendererParams<Row>) => (
      <span className="flex items-center gap-1.5">
        <span
          className="size-2.5 shrink-0 rounded-full border border-current/20"
          style={{ background: p.data?.color ?? 'transparent' }}
        />
        <span className="truncate">{p.value as string}</span>
      </span>
    ),
  },
  {
    field: 'volume',
    headerName: 'Volume',
    type: 'rightAligned',
    width: 110,
    valueFormatter: (p) => (p.value == null ? '–' : formatValue(p.value as number, 6)),
    cellClass: 'font-mono tabular',
  },
  {
    field: 'percent',
    headerName: '% of total',
    type: 'rightAligned',
    width: 96,
    valueFormatter: (p) => (p.value == null ? '–' : (p.value as number).toFixed(2)),
    cellClass: 'font-mono tabular',
  },
  {
    field: 'n_pixels',
    headerName: 'Pixels',
    type: 'rightAligned',
    width: 88,
    cellClass: 'font-mono tabular',
  },
]

const EXTENSIONS = {
  fid: ['.csv', '.txt'],
  ms: ['.cdf', '.csv', '.txt', '.jdx', '.dx', '.jcamp'],
}

/**
 * Batch (spec INT-4): integrate the masks on other files with the same
 * processing parameters; downloads a CSV with one row per file.
 */
async function batchIntegrate(doc: DocumentInfo) {
  const paths = await pickPaths({
    title: 'Batch integration',
    description: `Integrate these masks on other ${doc.kind.toUpperCase()} files, processed with the parameters of ${doc.name}. The CSV has one row per file.`,
    extensions: EXTENSIONS[doc.kind],
    confirm: 'Integrate',
  })
  if (!paths) return
  try {
    const { job } = await api().batchIntegrate(doc.id, paths)
    const done = await waitForJob(job)
    if (done.status !== 'done') return // failures are reported by the job
    const result = done.result as {
      csv: string
      n_files: number
      failed: { file: string; error: string }[]
    }
    const stem = doc.name.replace(/\.[^.]+$/, '') || 'chromatogram'
    saveFile({
      data: new TextEncoder().encode(result.csv).buffer as ArrayBuffer,
      filename: `${stem}_batch_integration.csv`,
      mediaType: 'text/csv',
    })
    const ok = result.n_files - result.failed.length
    if (result.failed.length) {
      notifyWarnings(
        `${ok} of ${result.n_files} files integrated`,
        result.failed.map((f) => `${f.file}: ${f.error}`),
      )
    } else {
      toast.success(`${ok} files integrated`, { description: 'The CSV has one row per file.' })
    }
  } catch (err) {
    notifyError('Batch integration', err)
  }
}

export function IntegrationPanel() {
  const doc = useActiveDocument()
  if (!doc) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
        No document
      </div>
    )
  }
  return <IntegrationView doc={doc} />
}

function IntegrationView({ doc }: { doc: DocumentInfo }) {
  const state = useSession((s) => s.integration[doc.id])
  const masks = useSession((s) => s.masks[doc.id]?.masks)
  const result = state?.result ?? null

  const colors = useMemo(() => new Map(masks?.map((m) => [m.id, m.color])), [masks])
  const rows: Row[] = useMemo(
    () => (result?.masks ?? []).map((r) => ({ ...r, color: colors.get(r.mask_id ?? '') ?? null })),
    [result, colors],
  )
  const pinned: Row[] = useMemo(
    () =>
      result
        ? [
            { ...result.unassigned, name: 'Unassigned', color: null },
            {
              mask_id: null,
              name: 'Total',
              volume: result.total,
              percent: 100,
              n_pixels: doc.grid.n_rt2 * doc.grid.n_mod,
              color: null,
            },
          ]
        : [],
    [result, doc.grid],
  )

  const copyPython = async () => {
    try {
      await navigator.clipboard.writeText(pythonSnippet(doc))
      toast.success('Python code copied', {
        description: `It loads the mask set from ${maskSetFilename(doc)}.`,
        action: {
          label: 'Save mask set',
          onClick: () =>
            void downloadExport(doc.id, { what: 'mask_set', format: 'json' }, 'Export mask set'),
        },
        duration: 10000,
      })
    } catch (err) {
      notifyError('Copy as Python', err)
    }
  }

  const signal =
    doc.kind === 'ms' ? (doc.signal === 'eic' ? `EIC m/z ${doc.mz?.join(', ')}` : 'TIC') : 'FID'

  return (
    <div className="flex h-full flex-col bg-panel" data-testid="integration-panel">
      <div className="flex h-9 shrink-0 items-center gap-1 border-b px-2 text-xs">
        <span className="text-muted-foreground">Volumes of the processed {signal} signal</span>
        <div className="ml-auto flex items-center gap-0.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Batch integration"
                disabled={!rows.length}
                onClick={() => void batchIntegrate(doc)}
                data-testid="batch"
              >
                <Files />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Batch: integrate these masks on other files</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Copy as Python"
                disabled={!rows.length}
                onClick={() => void copyPython()}
                data-testid="copy-python"
              >
                <ClipboardCopy />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Copy as Python (reproduces this integration)</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Export CSV"
                disabled={!rows.length}
                onClick={() =>
                  void downloadExport(
                    doc.id,
                    { what: 'integration', format: 'csv' },
                    'Export integration',
                  )
                }
                data-testid="integration-csv"
              >
                <Download />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Export CSV (one row per chromatogram)</TooltipContent>
          </Tooltip>
        </div>
      </div>
      {state?.error && (
        <p className="border-b bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Cannot integrate: {state.error}
        </p>
      )}
      {result?.overlaps.map((o) => (
        <p
          key={`${o.a}-${o.b}`}
          className="flex items-center gap-1.5 border-b bg-warning/10 px-3 py-1.5 text-xs"
          data-testid="overlap-warning"
        >
          <AlertTriangle className="size-3.5 shrink-0 text-warning" />
          <span>
            <b className="font-medium">{o.names[0]}</b> and{' '}
            <b className="font-medium">{o.names[1]}</b> share {o.n_pixels.toLocaleString()} pixels,
            counted in both.
          </span>
        </p>
      ))}
      {rows.length === 0 && !state?.error ? (
        <div className="flex flex-1 items-center justify-center p-4 text-center text-xs text-muted-foreground">
          Draw or import masks to integrate regions.
        </div>
      ) : (
        <div className="min-h-0 flex-1" data-testid="integration-grid">
          <DataGrid<Row>
            rowData={rows}
            pinnedBottomRowData={pinned}
            columnDefs={columns}
            getRowId={(p) => p.data.mask_id ?? p.data.name}
            onRowClicked={(e: RowClickedEvent<Row>) => {
              if (e.data?.mask_id) setActiveMask(doc.id, e.data.mask_id)
            }}
            domLayout="normal"
          />
        </div>
      )}
    </div>
  )
}
