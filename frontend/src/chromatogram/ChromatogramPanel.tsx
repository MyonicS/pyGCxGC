/** Chromatogram panel: toolbar and view of the active document. */

import {
  ChevronDown,
  Hand,
  Lasso,
  Layers,
  MousePointer2,
  Pentagon,
  Plus,
  ScanSearch,
  Shrink,
  Square,
  SquareMinus,
  Target,
} from 'lucide-react'
import { useMemo, useState } from 'react'

import type { DocumentInfo } from '@/api/types'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Toggle } from '@/components/ui/toggle'
import { Separator } from '@/components/ui/separator'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Kbd } from '@/components/Kbd'
import { useImages } from '@/store/images'
import { newMask, useActiveMaskId, useMasks } from '@/store/masks'
import { setSignal } from '@/store/ms'
import { useActiveDocument, useSession } from '@/store/session'
import {
  defaultDisplay,
  isDrawTool,
  resetZoom,
  setActiveMask,
  setSubtract,
  setTool,
  updateDisplay,
  useView,
  type Tool,
} from '@/store/view'

import { ChromView } from './ChromView'
import { DisplayPopover } from './DisplayPopover'

interface ToolInfo {
  tool: Tool
  label: string
  key: string
  icon: React.ReactNode
}

const VIEW_TOOLS: ToolInfo[] = [
  { tool: 'select', label: 'Select / inspect', key: 'V', icon: <MousePointer2 /> },
  { tool: 'pan', label: 'Pan', key: 'H', icon: <Hand /> },
  { tool: 'zoom', label: 'Zoom box', key: 'Z', icon: <ScanSearch /> },
]

/** Mask drawing tools (spec MASK-4). */
const DRAW_TOOLS: ToolInfo[] = [
  { tool: 'rect', label: 'Rectangle: drag', key: 'R', icon: <Square /> },
  { tool: 'lasso', label: 'Lasso: drag freehand', key: 'L', icon: <Lasso /> },
  {
    tool: 'polygon',
    label: 'Polygon: click vertices, double click or Enter closes, Esc cancels',
    key: 'P',
    icon: <Pentagon />,
  },
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
        <Tools tools={VIEW_TOOLS} tool={tool} label="View tool" />
        <Tools tools={DRAW_TOOLS} tool={tool} label="Mask drawing tool" />
        {isDrawTool(tool) && <DrawingTarget docId={doc.id} />}
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
        {doc.kind === 'ms' && <SignalInput key={`${doc.id}:${doc.mz?.join(',')}`} doc={doc} />}
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

function Tools({ tools, tool, label }: { tools: ToolInfo[]; tool: Tool; label: string }) {
  return (
    <ToggleGroup
      type="single"
      size="sm"
      spacing={0}
      variant="outline"
      value={tools.some((t) => t.tool === tool) ? tool : ''}
      onValueChange={(v) => v && setTool(v as Tool)}
      aria-label={label}
    >
      {tools.map((t) => (
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
  )
}

/** Subtract toggle and the mask new shapes go into (spec MASK-3/4). */
function DrawingTarget({ docId }: { docId: string }) {
  const subtract = useView((s) => s.subtract)
  const masks = useMasks(docId)
  const activeId = useActiveMaskId(docId)
  const active = masks.find((m) => m.id === activeId)
  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Toggle
            size="sm"
            variant="outline"
            pressed={subtract}
            onPressedChange={setSubtract}
            aria-label="Subtract"
            data-testid="subtract"
          >
            <SquareMinus />
          </Toggle>
        </TooltipTrigger>
        <TooltipContent>
          Subtract from the mask (or hold <Kbd>Alt</Kbd> while drawing)
        </TooltipContent>
      </Tooltip>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="max-w-44"
            aria-label="Mask to draw into"
            data-testid="draw-target"
          >
            <span
              className="size-2.5 shrink-0 rounded-full border border-current/30"
              style={{ background: active?.color ?? 'transparent' }}
            />
            <span className="truncate">{active ? active.name : 'New mask'}</span>
            <ChevronDown />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuLabel>Draw into</DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={activeId ?? ''}
            onValueChange={(id) => setActiveMask(docId, id)}
          >
            {masks.map((m) => (
              <DropdownMenuRadioItem key={m.id} value={m.id}>
                <span className="size-2.5 rounded-full" style={{ background: m.color }} />
                {m.name}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
          {masks.length > 0 && <DropdownMenuSeparator />}
          <DropdownMenuItem onSelect={() => void newMask(docId)}>
            <Plus /> New mask
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  )
}

/** TIC or EIC from an m/z list (spec MS-3); an empty field returns to the TIC. */
function SignalInput({ doc }: { doc: DocumentInfo }) {
  const current = doc.mz?.join(' ') ?? ''
  const [text, setText] = useState(current)
  const [busy, setBusy] = useState(false)
  const apply = async () => {
    if (text.trim() === current) return
    setBusy(true)
    const ok = await setSignal(doc, text)
    setBusy(false)
    if (!ok) setText(current)
  }
  return (
    <>
      <Separator orientation="vertical" className="mx-1 h-5!" />
      <Tooltip>
        <TooltipTrigger asChild>
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            EIC
            <Input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void apply()
                if (e.key === 'Escape') setText(current)
              }}
              onBlur={() => setText(current)}
              placeholder="TIC"
              aria-label="EIC m/z"
              disabled={busy}
              className="h-7 w-28 px-1.5 font-mono text-xs tabular"
              data-testid="eic-input"
            />
          </label>
        </TooltipTrigger>
        <TooltipContent>
          m/z of an extracted ion chromatogram, e.g. 91 or 57 71 85. Enter applies, empty = TIC.
        </TooltipContent>
      </Tooltip>
    </>
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
