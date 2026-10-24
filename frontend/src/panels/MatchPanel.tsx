/**
 * Match panel (spec MS-7): head-to-tail plot of the unknown (up) and the
 * selected hit (down), a header with the name and match badges and the
 * library switcher (LIB-2), and the structure card (§8) beside the plot when
 * the panel is wide (448 px), below it when narrow.
 */

import { useMemo } from 'react'

import type { DocumentInfo, HitInfo } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { clipMz } from '@/lib/spectrum'
import { LibraryPicker } from '@/ms/LibraryPicker'
import { StickPlot } from '@/ms/StickPlot'
import { StructureCard } from '@/ms/StructureCard'
import { useLibrarySpectrum, useSelectedHit, useSelectionSpectrum } from '@/store/ms'
import { useActiveDocument, useSession } from '@/store/session'

export function MatchPanel() {
  const doc = useActiveDocument()
  if (!doc || doc.kind !== 'ms') {
    return (
      <div className="flex h-full items-center justify-center bg-panel p-4 text-xs text-muted-foreground">
        {doc ? 'Matches need a GCxGC-MS document' : 'No document'}
      </div>
    )
  }
  return <MatchView doc={doc} />
}

function MatchView({ doc }: { doc: DocumentInfo }) {
  const sel = useSession((s) => s.selections[doc.id] ?? null)
  const hit = useSelectedHit(doc.id)
  const spectrum = useSelectionSpectrum(doc)
  const reference = useLibrarySpectrum(hit)
  const [lo, hi] = doc.mass_range ?? [0, 10000]
  // library peaks outside the acquired range are not compared (search too)
  const shown = useMemo(() => (reference ? clipMz(reference, lo, hi) : null), [reference, lo, hi])

  return (
    <div className="@container flex h-full flex-col bg-panel" data-testid="match-panel">
      <div className="flex min-h-9 shrink-0 items-center gap-2 border-b px-2 py-1">
        <MatchHeader hit={hit} />
        <LibraryPicker className="ml-auto shrink-0" />
      </div>
      <div className="flex min-h-0 flex-1 flex-col @md:flex-row">
        <div className="min-h-32 min-w-0 flex-1">
          <StickPlot
            top={
              spectrum
                ? {
                    sticks: spectrum.corrected,
                    label: 'Unknown',
                    stroke: 'stroke-plot-unknown',
                    fill: 'fill-plot-unknown',
                  }
                : null
            }
            bottom={
              shown
                ? {
                    sticks: shown,
                    label: hit?.library ?? 'Library',
                    stroke: 'stroke-plot-reference',
                    fill: 'fill-plot-reference',
                  }
                : null
            }
            empty={
              !sel
                ? 'Click the chromatogram to compare its spectrum with the library'
                : !hit
                  ? 'No hit selected'
                  : null
            }
            data-testid="head-to-tail"
          />
        </div>
        {hit && (
          <div className="max-h-[55%] shrink-0 overflow-auto border-t @md:max-h-none @md:w-[40%] @md:max-w-64 @md:border-t-0 @md:border-l">
            <StructureCard hit={hit} />
          </div>
        )}
      </div>
    </div>
  )
}

function MatchHeader({ hit }: { hit: HitInfo | undefined }) {
  if (!hit) return <span className="text-xs text-muted-foreground">Match</span>
  return (
    <div className="flex min-w-0 items-center gap-2" data-testid="match-header">
      <span className="truncate text-sm font-medium" title={hit.name}>
        {hit.name}
      </span>
      <Badge variant="secondary" className="font-mono tabular" title="Match factor">
        MF {Math.round(hit.match)}
      </Badge>
      <Badge variant="outline" className="font-mono tabular" title="Reverse match factor">
        RMF {Math.round(hit.reverse_match)}
      </Badge>
    </div>
  )
}
