/** Chromatogram panel: toolbar and view of the active document. */

import { Hand, Layers, MousePointer2, ScanSearch, Shrink, Target } from 'lucide-react'
import { useMemo } from 'react'

import type { DocumentInfo } from '@/api/types'

import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Kbd } from '@/components/Kbd'
import { useImages } from '@/store/images'
import { useActiveDocument, useSession } from '@/store/session'
import { defaultDisplay, resetZoom, setTool, updateDisplay, useView, type Tool } from '@/store/view'

import { ChromView } from './ChromView'
import { DisplayPopover } from './DisplayPopover'

const TOOLS: { tool: Tool; label: string; key: string; icon: React.ReactNode }[] = [
  { tool: 'select', label: 'Select / inspect', key: 'V', icon: <MousePointer2 /> },
  { tool: 'pan', label: 'Pan', key: 'H', icon: <Hand /> },
  { tool: 'zoom', label: 'Zoom box', key: 'Z', icon: <ScanSearch /> },
]

export function ChromatogramPanel() {
  const doc = useActiveDocument()
  const tool = useView((s) => s.tool)
  const settings = useSession((s) => s.settings)
  const stored = useView((s) => (doc ? s.display[doc.id] : undefined))
  const display = useMemo(() => stored ?? defaultDisplay(settings), [stored, settings])
  const image = useImages((s) => (doc ? s.images[doc.id] : undefined))
  const hasPeaks = useSession((s) => Boolean(doc && s.peaks[doc.id]?.rows.length))
  const hasMasks = useSession((s) => Boolean(doc && s.masks[doc.id]?.masks.length))

  if (!doc) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        No document
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col bg-panel">
      <div className="flex h-9 shrink-0 items-center gap-1 border-b px-2">
        <ToggleGroup
          type="single"
          size="sm"
          spacing={0}
          variant="outline"
          value={tool}
          onValueChange={(v) => v && setTool(v as Tool)}
          aria-label="Tool"
        >
          {TOOLS.map((t) => (
            <Tooltip key={t.tool}>
              <TooltipTrigger asChild>
                <ToggleGroupItem value={t.tool} aria-label={t.label} data-testid={`tool-${t.tool}`}>
                  {t.icon}
                </ToggleGroupItem>
              </TooltipTrigger>
              <TooltipContent>
                {t.label} <Kbd>{t.key}</Kbd>
              </TooltipContent>
            </Tooltip>
          ))}
        </ToggleGroup>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Reset zoom"
              onClick={() => resetZoom(doc.id)}
              data-testid="reset-zoom"
            >
              <Shrink />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            Reset zoom <Kbd>0</Kbd> or double click
          </TooltipContent>
        </Tooltip>
        <Separator orientation="vertical" className="mx-1 h-5!" />
        <DisplayPopover docId={doc.id} display={display} image={image} />
        <Separator orientation="vertical" className="mx-1 h-5!" />
        <OverlayToggle
          label="Masks"
          icon={<Layers />}
          pressed={display.showMasks}
          disabled={!hasMasks}
          onChange={(showMasks) => updateDisplay(doc.id, { showMasks })}
        />
        {doc.kind === 'ms' && (
          <OverlayToggle
            label="Peaks"
            icon={<Target />}
            pressed={display.showPeaks}
            disabled={!hasPeaks}
            onChange={(showPeaks) => updateDisplay(doc.id, { showPeaks })}
          />
        )}
        <div className="ml-auto truncate pl-2 text-xs text-muted-foreground">
          {signalLabel(doc)}
          {image && (image.overview.pool[0] > 1 || image.overview.pool[1] > 1)
            ? ` · overview max-pooled ${image.overview.pool[0]} × ${image.overview.pool[1]}`
            : ''}
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <ChromView doc={doc} />
      </div>
    </div>
  )
}

function signalLabel(doc: DocumentInfo): string {
  if (doc.kind === 'fid') return ''
  return doc.signal === 'eic' ? `EIC m/z ${doc.mz?.join(', ')}` : 'TIC'
}

function OverlayToggle({
  label,
  icon,
  pressed,
  disabled,
  onChange,
}: {
  label: string
  icon: React.ReactNode
  pressed: boolean
  disabled: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span>
          <Button
            variant={pressed && !disabled ? 'secondary' : 'ghost'}
            size="sm"
            aria-pressed={pressed}
            disabled={disabled}
            onClick={() => onChange(!pressed)}
          >
            {icon}
            {label}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>
        {disabled ? `No ${label.toLowerCase()} yet` : `Show ${label.toLowerCase()}`}
      </TooltipContent>
    </Tooltip>
  )
}
