/**
 * Processing parameters of the active document (spec FID-2, MS-2).
 *
 * FID parameters map 1:1 to `parse_2D_chromatogram`. Dragging the phase-shift
 * slider previews the shift in the shader (a cyclic roll of the rows, spec
 * FID-5) and applies it on release.
 */

import { AlertTriangle, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { DocumentInfo, ProcessingParams } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { notifyError } from '@/lib/notify'
import { useImages } from '@/store/images'
import { api, useActiveDocument, useSession, waitForJob } from '@/store/session'
import { setShiftPreview, useView } from '@/store/view'

type Draft = Record<string, string>

const FID_BASELINES = [
  ['stridewise', 'Stridewise'],
  ['global', 'Global minimum'],
  ['none', 'None'],
] as const
const FID_NORMALIZE = [
  ['volume', 'Volume'],
  ['max', 'Maximum'],
  ['none', 'None'],
] as const
const MS_IMAGE_BASELINES = [
  ['modulation_percentile', 'Modulation percentile'],
  ['rolling_percentile', 'Rolling percentile'],
  ['none', 'None'],
] as const
const MS_BASELINES = [
  ...MS_IMAGE_BASELINES,
  ['local', 'Local (spectra only)'],
  ['reference', 'Reference point (spectra only)'],
] as const

interface MSBaseline {
  mode: string
  percentile?: number
  window?: number
  gap?: number
  width?: number
  scan?: number
  n_scans?: number
  image_model?: MSBaseline
}

/** Modes that change spectra only; the image uses `image_model` (spec BASE-2). */
const spectraOnly = (mode: string) => mode === 'local' || mode === 'reference'

function draftOf(doc: DocumentInfo): Draft {
  const p = doc.params as Record<string, unknown>
  const str = (v: unknown) => (v === null || v === undefined ? '' : String(v))
  if (doc.kind === 'fid') {
    return {
      modulation_time: str(p.modulation_time),
      sampling_interval: str(p.sampling_interval),
      shift: str(p.shift ?? 0),
      solvent_cutoff: str(p.solvent_cutoff ?? 0),
      baseline: str(p.baseline ?? 'stridewise'),
      normalize: str(p.normalize ?? 'volume'),
    }
  }
  const b = (p.baseline ?? {}) as MSBaseline
  const mode = b.mode ?? 'modulation_percentile'
  const image = spectraOnly(mode)
    ? (b.image_model ?? { mode: 'modulation_percentile', percentile: 25 })
    : b
  return {
    modulation_time: str(p.modulation_time),
    shift: str(p.shift ?? 0),
    baseline: str(mode),
    image: str(image.mode),
    percentile: str(image.percentile ?? 25),
    window: str(image.window ?? 2),
    gap: str(b.gap ?? 3),
    width: str(b.width ?? 5),
    ref_scan: str(b.scan ?? ''),
    n_scans: str(b.n_scans ?? 3),
    snr: str(p.snr ?? 3),
  }
}

/** Parameters that differ from the document, or an error message. */
function changesOf(doc: DocumentInfo, draft: Draft): ProcessingParams | string {
  const current = draftOf(doc)
  const num = (key: string, label: string, opts: { int?: boolean; min?: number } = {}) => {
    const v = Number(draft[key])
    if (draft[key].trim() === '' || !Number.isFinite(v)) throw new Error(`${label}: enter a number`)
    if (opts.int && !Number.isInteger(v)) throw new Error(`${label}: enter a whole number`)
    if (opts.min !== undefined && v < opts.min) throw new Error(`${label}: at least ${opts.min}`)
    return v
  }
  try {
    const out: Record<string, unknown> = {}
    const changed = (key: string) => draft[key] !== current[key]
    if (changed('modulation_time')) out.modulation_time = num('modulation_time', 'Modulation time')
    if (changed('shift')) out.shift = num('shift', 'Phase shift', { int: true })
    if (doc.kind === 'fid') {
      if (changed('sampling_interval'))
        out.sampling_interval = num('sampling_interval', 'Sampling interval')
      if (changed('solvent_cutoff'))
        out.solvent_cutoff = num('solvent_cutoff', 'Solvent cutoff', { min: 0 })
      if (changed('baseline')) out.baseline = draft.baseline
      if (changed('normalize')) out.normalize = draft.normalize
    } else {
      if (changed('snr')) out.snr = num('snr', 'S/N threshold', { min: 0 })
      const keys = [
        'baseline',
        'image',
        'percentile',
        'window',
        'gap',
        'width',
        'ref_scan',
        'n_scans',
      ]
      if (keys.some(changed)) {
        const imageMode = spectraOnly(draft.baseline) ? draft.image : draft.baseline
        const image: MSBaseline = { mode: imageMode }
        if (imageMode !== 'none') image.percentile = num('percentile', 'Percentile', { min: 0 })
        if (imageMode === 'rolling_percentile') {
          image.window = num('window', 'Window', { int: true, min: 0 })
        }
        let baseline = image
        if (draft.baseline === 'local') {
          baseline = {
            mode: 'local',
            gap: num('gap', 'Gap', { int: true, min: 1 }),
            width: num('width', 'Width', { int: true, min: 1 }),
            image_model: image,
          }
        } else if (draft.baseline === 'reference') {
          if (draft.ref_scan.trim() === '') {
            throw new Error(
              'Reference scan: pick one with "Set as background" in the Spectrum panel',
            )
          }
          baseline = {
            mode: 'reference',
            scan: num('ref_scan', 'Reference scan', { int: true, min: 0 }),
            n_scans: num('n_scans', 'Scans', { int: true, min: 1 }),
            image_model: image,
          }
        }
        out.baseline = baseline as ProcessingParams['baseline']
      }
    }
    return out as ProcessingParams
  } catch (err) {
    return (err as Error).message
  }
}

export function ProcessingPanel() {
  const doc = useActiveDocument()
  if (!doc) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
        No document
      </div>
    )
  }
  // a new form (fresh draft) whenever the document was re-processed
  return <ProcessingForm key={`${doc.id}:${JSON.stringify(doc.params)}`} doc={doc} />
}

function ProcessingForm({ doc }: { doc: DocumentInfo }) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(doc))
  const [busy, setBusy] = useState(false)
  const preview = useView((s) => s.shiftPreview[doc.id])
  const imageShift = useImages((s) => s.images[doc.id]?.shift)
  const hasJob = useSession((s) =>
    Object.values(s.jobs).some(
      (j) => j.document_id === doc.id && (j.status === 'running' || j.status === 'queued'),
    ),
  )

  // the preview ends once the image with that shift has arrived
  useEffect(() => {
    if (preview !== undefined && imageShift === preview) setShiftPreview(doc.id, undefined)
  }, [preview, imageShift, doc.id])

  const changes = changesOf(doc, draft)
  const error = typeof changes === 'string' ? changes : null
  const dirty = typeof changes !== 'string' && Object.keys(changes).length > 0
  const geometryChanged =
    typeof changes !== 'string' &&
    (changes.modulation_time !== undefined ||
      changes.shift !== undefined ||
      changes.sampling_interval !== undefined)

  const apply = async (params: ProcessingParams) => {
    setBusy(true)
    try {
      const { job } = await api().updateDocument(doc.id, params)
      const done = await waitForJob(job)
      if (done.status !== 'done') setShiftPreview(doc.id, undefined)
    } catch (err) {
      setShiftPreview(doc.id, undefined)
      notifyError('Processing', err)
    } finally {
      setBusy(false)
    }
  }

  const set = (key: string) => (value: string) => setDraft((d) => ({ ...d, [key]: value }))
  const n = doc.grid.n_rt2
  const shiftValue = Number(draft.shift) || 0
  const fid = doc.kind === 'fid'

  return (
    <form
      className="flex h-full flex-col overflow-auto bg-panel"
      onSubmit={(e) => {
        e.preventDefault()
        if (typeof changes !== 'string' && dirty) void apply(changes)
      }}
      data-testid="processing-panel"
    >
      <div className="flex flex-col gap-3 p-3">
        <Row label="Modulation time" unit="s" htmlFor="p-mod">
          <Input
            id="p-mod"
            value={draft.modulation_time}
            onChange={(e) => set('modulation_time')(e.target.value)}
            inputMode="decimal"
            className="h-7 font-mono text-xs tabular"
          />
        </Row>
        {fid && (
          <Row label="Sampling interval" unit="s" htmlFor="p-dt" hint="Inferred from the data">
            <Input
              id="p-dt"
              value={draft.sampling_interval}
              onChange={(e) => set('sampling_interval')(e.target.value)}
              inputMode="decimal"
              className="h-7 font-mono text-xs tabular"
            />
          </Row>
        )}
        <div className="flex flex-col gap-1.5">
          <Row label="Phase shift" unit={fid ? 'points' : 'scans'} htmlFor="p-shift">
            <Input
              id="p-shift"
              value={draft.shift}
              onChange={(e) => set('shift')(e.target.value)}
              inputMode="numeric"
              className="h-7 font-mono text-xs tabular"
            />
          </Row>
          {fid && (
            <Slider
              aria-label="Phase shift preview"
              data-testid="shift-slider"
              min={-Math.floor(n / 2)}
              max={Math.floor(n / 2)}
              step={1}
              value={[Math.max(-n / 2, Math.min(n / 2, preview ?? shiftValue))]}
              disabled={busy || hasJob}
              onValueChange={([v]) => {
                setShiftPreview(doc.id, v)
                set('shift')(String(v))
              }}
              onValueCommit={([v]) => {
                const params = changesOf(doc, { ...draft, shift: String(v) })
                if (typeof params !== 'string' && params.shift !== undefined) {
                  void apply({ shift: params.shift })
                } else {
                  setShiftPreview(doc.id, undefined)
                }
              }}
            />
          )}
        </div>
        {fid ? (
          <>
            <Row label="Solvent cutoff" unit="min" htmlFor="p-solvent">
              <Input
                id="p-solvent"
                value={draft.solvent_cutoff}
                onChange={(e) => set('solvent_cutoff')(e.target.value)}
                inputMode="decimal"
                className="h-7 font-mono text-xs tabular"
              />
            </Row>
            <Row label="Baseline">
              <Choice value={draft.baseline} options={FID_BASELINES} onChange={set('baseline')} />
            </Row>
            <Row label="Normalisation">
              <Choice value={draft.normalize} options={FID_NORMALIZE} onChange={set('normalize')} />
            </Row>
          </>
        ) : (
          <>
            <Row label="Baseline">
              <Choice value={draft.baseline} options={MS_BASELINES} onChange={set('baseline')} />
            </Row>
            {draft.baseline === 'local' && (
              <>
                <Row label="Gap" unit="scans" htmlFor="p-gap" hint="Point to background">
                  <Input
                    id="p-gap"
                    value={draft.gap}
                    onChange={(e) => set('gap')(e.target.value)}
                    inputMode="numeric"
                    className="h-7 font-mono text-xs tabular"
                  />
                </Row>
                <Row label="Width" unit="scans" htmlFor="p-width" hint="Background on each side">
                  <Input
                    id="p-width"
                    value={draft.width}
                    onChange={(e) => set('width')(e.target.value)}
                    inputMode="numeric"
                    className="h-7 font-mono text-xs tabular"
                  />
                </Row>
              </>
            )}
            {draft.baseline === 'reference' && (
              <>
                <Row
                  label="Reference scan"
                  htmlFor="p-ref"
                  hint="Spectrum panel: Set as background"
                >
                  <Input
                    id="p-ref"
                    value={draft.ref_scan}
                    onChange={(e) => set('ref_scan')(e.target.value)}
                    inputMode="numeric"
                    className="h-7 font-mono text-xs tabular"
                  />
                </Row>
                <Row label="Scans" htmlFor="p-nscans" hint="Averaged around it">
                  <Input
                    id="p-nscans"
                    value={draft.n_scans}
                    onChange={(e) => set('n_scans')(e.target.value)}
                    inputMode="numeric"
                    className="h-7 font-mono text-xs tabular"
                  />
                </Row>
              </>
            )}
            {spectraOnly(draft.baseline) && (
              <Row label="Image baseline" hint="Image, EICs and peaks">
                <Choice value={draft.image} options={MS_IMAGE_BASELINES} onChange={set('image')} />
              </Row>
            )}
            {(spectraOnly(draft.baseline) ? draft.image : draft.baseline) !== 'none' && (
              <Row label="Percentile" htmlFor="p-pct">
                <Input
                  id="p-pct"
                  value={draft.percentile}
                  onChange={(e) => set('percentile')(e.target.value)}
                  inputMode="decimal"
                  className="h-7 font-mono text-xs tabular"
                />
              </Row>
            )}
            {(spectraOnly(draft.baseline) ? draft.image : draft.baseline) ===
              'rolling_percentile' && (
              <Row label="Window" unit="modulations" htmlFor="p-window">
                <Input
                  id="p-window"
                  value={draft.window}
                  onChange={(e) => set('window')(e.target.value)}
                  inputMode="numeric"
                  className="h-7 font-mono text-xs tabular"
                />
              </Row>
            )}
            <Row
              label="Channel S/N"
              htmlFor="p-snr"
              hint="Channels below S/N × noise are zeroed; 0 = off"
            >
              <Input
                id="p-snr"
                value={draft.snr}
                onChange={(e) => set('snr')(e.target.value)}
                inputMode="decimal"
                className="h-7 font-mono text-xs tabular"
              />
            </Row>
          </>
        )}

        {geometryChanged && doc.n_masks > 0 && (
          <p className="flex gap-1.5 rounded-md bg-warning/10 p-2 text-xs text-foreground">
            <AlertTriangle className="size-4 shrink-0 text-warning" />
            Masks are drawn in retention time units. Changing the modulation, sampling or phase
            shift moves the data under them.
          </p>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}

        <div className="flex gap-2">
          <Button
            type="submit"
            size="sm"
            disabled={!dirty || busy || hasJob}
            data-testid="apply-processing"
          >
            {(busy || hasJob) && <Loader2 className="animate-spin" />}
            Apply
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={!dirty && !error}
            onClick={() => {
              setDraft(draftOf(doc))
              setShiftPreview(doc.id, undefined)
            }}
          >
            Reset
          </Button>
        </div>
        {doc.warnings.length > 0 && (
          <ul className="list-disc pl-4 text-xs text-muted-foreground">
            {doc.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
      </div>
    </form>
  )
}

function Row({
  label,
  unit,
  hint,
  htmlFor,
  children,
}: {
  label: string
  unit?: string
  hint?: string
  htmlFor?: string
  children: React.ReactNode
}) {
  return (
    <div className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] items-center gap-2">
      <Label htmlFor={htmlFor} className="flex flex-col items-start gap-0 text-xs" title={hint}>
        <span>
          {label}
          {unit && <span className="text-muted-foreground"> ({unit})</span>}
        </span>
        {hint && <span className="text-[10px] font-normal text-muted-foreground">{hint}</span>}
      </Label>
      {children}
    </div>
  )
}

function Choice({
  value,
  options,
  onChange,
}: {
  value: string
  options: readonly (readonly [string, string])[]
  onChange: (v: string) => void
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger size="sm" className="h-7 w-full text-xs">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map(([v, label]) => (
          <SelectItem key={v} value={v} className="text-xs">
            {label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
