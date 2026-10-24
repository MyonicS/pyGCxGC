/**
 * Spectrum panel (spec MS-5): stick spectrum of the selected point or mask,
 * corrected or raw, relative (999) or absolute. The header shows RT1, RT2,
 * scan, averaged scans and the baseline in force (BASE-4). The menu copies
 * or saves the spectrum (MS-10) and sets the point as background (BASE-2).
 */

import { ClipboardCopy, Crosshair, Download, Eraser, ImageDown, MoreHorizontal } from 'lucide-react'
import { useState } from 'react'

import type { DocumentInfo, SelectionInfo } from '@/api/types'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Toggle } from '@/components/ui/toggle'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { FigureMenuItems } from '@/components/FigureMenu'
import { figureName, saveSvgPlot, type FigureFormat, type FigureTheme } from '@/lib/figure'
import { notifyError } from '@/lib/notify'
import { StickPlot } from '@/ms/StickPlot'
import {
  copySpectrum,
  downloadSpectrum,
  setAsBackground,
  setPointSettings,
  setSpectrumDisplay,
  useMsView,
  usePointSettings,
  useSelectionSpectrum,
} from '@/store/ms'
import { useActiveDocument, useSession } from '@/store/session'

export function SpectrumPanel() {
  const doc = useActiveDocument()
  if (!doc || doc.kind !== 'ms') {
    return (
      <div className="flex h-full items-center justify-center bg-panel p-4 text-xs text-muted-foreground">
        {doc ? 'Spectra need a GCxGC-MS document' : 'No document'}
      </div>
    )
  }
  return <SpectrumView doc={doc} />
}

function SpectrumView({ doc }: { doc: DocumentInfo }) {
  const sel = useSession((s) => s.selections[doc.id] ?? null)
  const spectrum = useSelectionSpectrum(doc)
  const corrected = useMsView((s) => s.corrected)
  const relative = useMsView((s) => s.relative)
  const sticks = spectrum ? (corrected ? spectrum.corrected : spectrum.raw) : null

  return (
    <div className="flex h-full flex-col bg-panel" data-testid="spectrum-panel">
      <div className="flex h-9 shrink-0 items-center gap-1 border-b px-2">
        <ToggleGroup
          type="single"
          size="sm"
          variant="outline"
          spacing={0}
          value={corrected ? 'corrected' : 'raw'}
          onValueChange={(v) => v && setSpectrumDisplay({ corrected: v === 'corrected' })}
          aria-label="Spectrum"
        >
          <ToggleGroupItem value="corrected" className="px-2 text-xs">
            Corrected
          </ToggleGroupItem>
          <ToggleGroupItem value="raw" className="px-2 text-xs">
            Raw
          </ToggleGroupItem>
        </ToggleGroup>
        <ToggleGroup
          type="single"
          size="sm"
          variant="outline"
          spacing={0}
          value={relative ? 'relative' : 'absolute'}
          onValueChange={(v) => v && setSpectrumDisplay({ relative: v === 'relative' })}
          aria-label="Intensity"
        >
          <ToggleGroupItem value="relative" className="px-2 text-xs" title="Base peak = 999">
            Relative
          </ToggleGroupItem>
          <ToggleGroupItem value="absolute" className="px-2 text-xs">
            Absolute
          </ToggleGroupItem>
        </ToggleGroup>
        <PointSettings />
        <SpectrumMenu doc={doc} sel={sel} />
      </div>
      <SelectionHeader sel={sel} />
      <div className="min-h-0 flex-1">
        <StickPlot
          top={
            sticks
              ? {
                  sticks,
                  label: corrected ? 'corrected' : 'raw',
                  stroke: 'stroke-plot-unknown',
                  fill: 'fill-plot-unknown',
                }
              : null
          }
          relative={relative}
          empty={
            sel
              ? spectrum
                ? 'Empty spectrum'
                : 'Loading…'
              : 'Click the chromatogram to see a spectrum'
          }
          data-testid="spectrum-plot"
        />
      </div>
    </div>
  )
}

/** Snap to apex and the number of averaged scans (spec MS-4). */
function PointSettings() {
  const { snap, nAverage } = usePointSettings()
  const [draft, setDraft] = useState<string | null>(null)
  const commit = () => {
    if (draft === null) return
    const n = Number(draft)
    if (Number.isInteger(n) && n >= 1 && n <= 101) setPointSettings({ n_average: n })
    setDraft(null)
  }
  return (
    <div className="ml-auto flex items-center gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <Toggle
            size="sm"
            variant="outline"
            pressed={snap}
            onPressedChange={(v) => setPointSettings({ snap: v })}
            aria-label="Snap to apex"
            data-testid="snap"
          >
            <Crosshair />
          </Toggle>
        </TooltipTrigger>
        <TooltipContent>Snap to apex: a click moves to the most intense scan nearby</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            Avg
            <Input
              value={draft ?? String(nAverage)}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => e.key === 'Enter' && commit()}
              inputMode="numeric"
              aria-label="Averaged scans"
              className="h-7 w-11 px-1.5 text-center font-mono text-xs tabular"
            />
          </label>
        </TooltipTrigger>
        <TooltipContent>Scans summed around the point</TooltipContent>
      </Tooltip>
    </div>
  )
}

function saveSpectrumFigure(
  doc: DocumentInfo,
  sel: SelectionInfo,
  format: FigureFormat,
  theme: FigureTheme,
) {
  const where = sel.region ? `mask-${sel.region.name}` : `scan${sel.scan}`
  saveSvgPlot(
    document.querySelector('[data-testid="spectrum-plot"]'),
    figureName(doc.name, where),
    format,
    theme,
  ).catch((err: unknown) => notifyError('Save figure', err))
}

function SpectrumMenu({ doc, sel }: { doc: DocumentInfo; sel: SelectionInfo | null }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Spectrum menu" disabled={!sel}>
          <MoreHorizontal />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => void copySpectrum(doc.id)}>
          <ClipboardCopy /> Copy as text
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void downloadSpectrum(doc.id)}>
          <Download /> Save as MSP
        </DropdownMenuItem>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <ImageDown /> Save figure
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <FigureMenuItems
              onSave={(format, theme) => sel && saveSpectrumFigure(doc, sel, format, theme)}
            />
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={!sel || sel.scan < 0}
          onSelect={() => sel && void setAsBackground(doc, sel)}
        >
          <Eraser /> Set as background
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function SelectionHeader({ sel }: { sel: SelectionInfo | null }) {
  if (!sel) return null
  const rt = (v: number | null | undefined, digits: number) =>
    v === null || v === undefined ? '–' : v.toFixed(digits)
  return (
    <div
      className="flex shrink-0 flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b px-2 py-1 text-[11px] text-muted-foreground"
      data-testid="spectrum-header"
    >
      {sel.region ? (
        <span className="text-foreground">
          Mask <span className="font-medium">{sel.region.name}</span> ·{' '}
          {sel.region.n_pixels.toLocaleString()} pixels
        </span>
      ) : (
        <span className="font-mono text-foreground tabular">
          RT1 {rt(sel.rt1, 3)} min · RT2 {rt(sel.rt2, 3)} s
        </span>
      )}
      {!sel.region && (
        <span className="tabular">
          scan {sel.scan} · {sel.n_average} {sel.n_average === 1 ? 'scan' : 'scans'}
          {sel.snap ? ' · snapped' : ''}
        </span>
      )}
      <span className="truncate" title={sel.baseline}>
        baseline {sel.baseline}
        {sel.snr > 0 ? ` · S/N ${sel.snr}` : ''}
      </span>
    </div>
  )
}
