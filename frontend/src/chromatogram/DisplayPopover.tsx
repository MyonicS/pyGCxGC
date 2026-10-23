/**
 * Display popover of the chromatogram toolbar (spec VIEW-4..7): colour map,
 * scaling, contrast and interpolation, plus mask overlay opacity. Every change
 * applies instantly, without the backend.
 */

import { Lock, LockOpen, SlidersHorizontal, Wand2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { COLORMAPS, colormapGradient } from '@/lib/colormaps'
import { SCALING_LABELS, SCALINGS, type Scaling } from '@/lib/scaling'
import { formatValue } from '@/lib/ticks'
import { cn } from '@/lib/utils'
import type { DocImage } from '@/store/images'
import {
  effectiveContrast,
  INTERPOLATION_LABELS,
  INTERPOLATIONS,
  setContrast,
  setLock,
  updateDisplay,
  useView,
  type Display,
  type Interpolation,
} from '@/store/view'

import { Histogram } from './Histogram'
import { autoVmax } from './imageMath'

const WIDTH = 296

export function DisplayPopover({
  docId,
  display,
  image,
}: {
  docId: string
  display: Display
  image: DocImage | undefined
}) {
  const lock = useView((s) => s.lock)
  const domain = useView((s) => s.domains[docId])
  const { vmin, vmax } = effectiveContrast(display, lock)

  const auto = () => {
    if (!image) return
    const max = autoVmax(image, domain)
    if (lock) setContrast(docId, 0, max)
    updateDisplay(docId, { vmin: 0, vmax: max, contrast: 'auto' })
  }

  return (
    <Popover>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="sm" data-testid="display-button">
              <SlidersHorizontal />
              Display
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Colour map, scaling, contrast, interpolation</TooltipContent>
      </Tooltip>
      <PopoverContent align="start" className="w-auto p-3" data-testid="display-popover">
        <div className="flex flex-col gap-3" style={{ width: WIDTH }}>
          <Section title="Colour map">
            <div className="grid grid-cols-2 gap-1">
              {COLORMAPS.map((name) => (
                <button
                  key={name}
                  type="button"
                  aria-pressed={display.colormap === name}
                  className={cn(
                    'flex flex-col gap-0.5 rounded-md border border-transparent p-1 text-left text-xs hover:bg-accent',
                    display.colormap === name && 'border-ring bg-accent',
                  )}
                  onClick={() => updateDisplay(docId, { colormap: name })}
                >
                  <span
                    className="h-3 w-full rounded-sm"
                    style={{ backgroundImage: colormapGradient(name, display.reverse) }}
                  />
                  <span>{name}</span>
                </button>
              ))}
            </div>
            <label className="flex items-center justify-between text-xs">
              Reverse
              <Switch
                checked={display.reverse}
                onCheckedChange={(reverse) => updateDisplay(docId, { reverse })}
              />
            </label>
          </Section>

          <Section title="Scaling">
            <Segmented
              value={display.scaling}
              options={SCALINGS}
              labels={SCALING_LABELS}
              onChange={(scaling: Scaling) => updateDisplay(docId, { scaling })}
            />
            {display.scaling === 'log' && (
              <NumberField
                label="Dynamic range k"
                value={display.k}
                onCommit={(k) => k > 0 && updateDisplay(docId, { k })}
              />
            )}
          </Section>

          <Section
            title="Contrast"
            action={
              <div className="flex gap-1">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="outline" size="xs" onClick={auto} data-testid="auto-contrast">
                      <Wand2 />
                      Auto
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    vmin = 0, vmax = 99.9th percentile of the visible data
                  </TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant={lock ? 'secondary' : 'outline'}
                      size="xs"
                      aria-pressed={Boolean(lock)}
                      onClick={() => setLock(docId, !lock)}
                    >
                      {lock ? <Lock /> : <LockOpen />}
                      Lock
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Keep this contrast when switching documents</TooltipContent>
                </Tooltip>
              </div>
            }
          >
            {image ? (
              <Histogram
                data={image.overview.data}
                range={image.range}
                vmin={vmin}
                vmax={vmax}
                scaling={display.scaling}
                k={display.k}
                colormap={display.colormap}
                reverse={display.reverse}
                width={WIDTH}
                onChange={(a, b) => setContrast(docId, a, b)}
              />
            ) : null}
            <div className="grid grid-cols-2 gap-2">
              <NumberField
                label="vmin"
                value={vmin}
                onCommit={(v) => setContrast(docId, v, Math.max(v, vmax))}
              />
              <NumberField
                label="vmax"
                value={vmax}
                onCommit={(v) => setContrast(docId, Math.min(vmin, v), v)}
              />
            </div>
          </Section>

          <Section title="Interpolation">
            <Segmented
              value={display.interpolation}
              options={INTERPOLATIONS}
              labels={INTERPOLATION_LABELS}
              onChange={(interpolation: Interpolation) => updateDisplay(docId, { interpolation })}
            />
          </Section>

          <Section title="Mask overlay">
            <div className="flex items-center gap-3">
              <Slider
                value={[display.maskOpacity]}
                min={0}
                max={1}
                step={0.05}
                onValueChange={([maskOpacity]) => updateDisplay(docId, { maskOpacity })}
                aria-label="Mask opacity"
              />
              <span className="w-9 text-right font-mono text-xs tabular">
                {Math.round(display.maskOpacity * 100)}%
              </span>
            </div>
          </Section>
        </div>
      </PopoverContent>
    </Popover>
  )
}

function Section({
  title,
  action,
  children,
}: {
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-1.5">
      <div className="flex h-6 items-center justify-between">
        <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

function Segmented<T extends string>({
  value,
  options,
  labels,
  onChange,
}: {
  value: T
  options: readonly T[]
  labels: Record<T, string>
  onChange: (v: T) => void
}) {
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      spacing={0}
      value={value}
      onValueChange={(v) => v && onChange(v as T)}
      className="w-full"
    >
      {options.map((o) => (
        <ToggleGroupItem key={o} value={o} className="flex-1 text-xs">
          {labels[o]}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

/** A number input that commits on Enter or blur and accepts "1e5". */
export function NumberField({
  label,
  value,
  onCommit,
  unit,
  id,
}: {
  label: string
  value: number
  onCommit: (v: number) => void
  unit?: string
  id?: string
}) {
  // the typed text while editing; otherwise the formatted value
  const [draft, setDraft] = useState<string | null>(null)
  const text = draft ?? (Number.isFinite(value) ? formatValue(value, 4) : '')
  const commit = () => {
    if (draft === null) return
    const v = Number(draft)
    setDraft(null)
    if (draft.trim() !== '' && Number.isFinite(v)) onCommit(v)
  }
  const inputId = id ?? `nf-${label.replace(/\W+/g, '-')}`
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={inputId} className="text-xs text-muted-foreground">
        {label}
        {unit ? ` (${unit})` : ''}
      </Label>
      <Input
        id={inputId}
        value={text}
        inputMode="decimal"
        className="h-7 font-mono text-xs tabular"
        onFocus={() => setDraft(text)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') {
            e.stopPropagation()
            setDraft(null)
          }
        }}
      />
    </div>
  )
}
