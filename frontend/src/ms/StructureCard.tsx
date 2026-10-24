/**
 * Structure card (spec STRUCT-2..4): the depiction of the hit's structure,
 * name, formula, MW, CAS and NIST#, *Copy SMILES* and a PubChem link. The
 * structure comes from the library entry, or (opt-in) from PubChem; the
 * one-time question is asked here, never in a modal.
 */

import { ClipboardCopy, ExternalLink, FlaskConical, Globe, Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import type { HitInfo, StructureInfo } from '@/api/types'
import { Button } from '@/components/ui/button'
import { useElementSize } from '@/lib/useElementSize'
import { useResolvedTheme } from '@/shell/theme'
import { setOnlineStructures, useStructure, useStructureSvg } from '@/store/ms'

export function StructureCard({ hit }: { hit: HitInfo }) {
  const { info, loading, error, refresh } = useStructure(hit)
  const [ref, size] = useElementSize<HTMLDivElement>()
  const theme = useResolvedTheme()
  // fixed steps, so resizing the panel does not refetch every pixel
  const w = Math.max(120, Math.min(280, Math.floor(size.width / 40) * 40))
  const h = Math.round(w * 0.72)
  const structure = info?.status === 'found' ? info.structure : null
  const svg = useStructureSvg(structure && info?.depiction ? structure.key : null, theme, w, h)

  return (
    <div
      className="flex h-full flex-col gap-2 overflow-auto p-2 text-xs"
      data-testid="structure-card"
    >
      <div ref={ref} className="flex min-h-24 items-center justify-center" style={{ height: h }}>
        {svg ? (
          <img
            src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`}
            width={w}
            height={h}
            alt={`Structure of ${hit.name}`}
            className="max-h-full max-w-full"
            data-testid="structure-image"
          />
        ) : (
          <Placeholder
            info={info}
            loading={loading}
            error={error}
            onAnswer={(online) => {
              void setOnlineStructures(online ? 'on' : 'off').then(refresh)
            }}
          />
        )}
      </div>
      <Details hit={hit} info={info} />
    </div>
  )
}

function Placeholder({
  info,
  loading,
  error,
  onAnswer,
}: {
  info: StructureInfo | null
  loading: boolean
  error: string | null
  onAnswer: (online: boolean) => void
}) {
  const status = info?.status
  if (status === 'ask') {
    return (
      <div className="flex flex-col items-center gap-2 text-center" data-testid="structure-ask">
        <Globe className="size-5 text-muted-foreground" />
        <p className="max-w-52 text-muted-foreground">
          Fetch structures from PubChem? Only compound identifiers of library hits are sent.
        </p>
        <div className="flex gap-1.5">
          <Button size="sm" onClick={() => onAnswer(true)}>
            Fetch
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onAnswer(false)}>
            Not now
          </Button>
        </div>
      </div>
    )
  }
  if (loading || status === 'pending') {
    return (
      <span className="flex items-center gap-1.5 text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        {status === 'pending' ? 'Looking up at PubChem…' : 'Loading…'}
      </span>
    )
  }
  return (
    <div className="flex flex-col items-center gap-1 text-center text-muted-foreground">
      <FlaskConical className="size-5 opacity-60" />
      <span>
        {error
          ? 'Structure lookup failed'
          : status === 'found'
            ? 'Install RDKit to draw structures'
            : 'No structure available'}
      </span>
      {info?.formula && <span className="font-mono text-foreground">{formula(info.formula)}</span>}
    </div>
  )
}

/** Formula with subscript digits. */
function formula(f: string) {
  return f.replace(/\d/g, (d) => '₀₁₂₃₄₅₆₇₈₉'[Number(d)])
}

function Details({ hit, info }: { hit: HitInfo; info: StructureInfo | null }) {
  const s = info?.status === 'found' ? info.structure : null
  const rows: [string, React.ReactNode][] = [
    ['Formula', hit.formula ? formula(hit.formula) : '–'],
    ['MW', hit.mw || '–'],
    ['CAS', hit.cas || '–'],
  ]
  if (hit.nist_no) rows.push(['NIST#', hit.nist_no])
  return (
    <div className="flex flex-col gap-1.5">
      <div className="font-medium break-words text-foreground">{hit.name}</div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="font-mono tabular">{v}</dd>
          </div>
        ))}
      </dl>
      {s && (
        <div className="flex flex-wrap gap-1">
          {s.smiles && (
            <Button
              size="xs"
              variant="outline"
              onClick={() =>
                void navigator.clipboard
                  .writeText(s.smiles)
                  .then(() => toast.success('SMILES copied', { description: s.smiles }))
              }
            >
              <ClipboardCopy /> Copy SMILES
            </Button>
          )}
          {s.pubchem_cid > 0 && (
            <Button size="xs" variant="outline" asChild>
              <a
                href={`https://pubchem.ncbi.nlm.nih.gov/compound/${s.pubchem_cid}`}
                target="_blank"
                rel="noreferrer noopener"
              >
                <ExternalLink /> PubChem
              </a>
            </Button>
          )}
        </div>
      )}
      {s && (
        <p className="text-[10px] text-muted-foreground" data-testid="structure-source">
          Structure: {s.source}
        </p>
      )}
    </div>
  )
}
