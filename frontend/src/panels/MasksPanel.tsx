/**
 * Masks panel (spec MASK-3): masks with colour, visibility, name (renamed in
 * place), pixel count and volume; create, duplicate, delete and reorder; one
 * mask is active. Below, the shapes of the active mask (MASK-6). Undo/redo
 * (MASK-5), import (INT-3) and export (INT-2).
 */

import {
  ArrowDown,
  ArrowUp,
  Copy,
  Download,
  Eye,
  EyeOff,
  FolderInput,
  Minus,
  Plus,
  Redo2,
  Trash2,
  Undo2,
} from 'lucide-react'
import { useState } from 'react'

import type { DocumentInfo, MaskInfo, OperationInfo } from '@/api/types'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Kbd } from '@/components/Kbd'
import { describeShape } from '@/chromatogram/shapes'
import { formatValue } from '@/lib/ticks'
import { cn } from '@/lib/utils'
import {
  deleteMask,
  deleteShape,
  downloadExport,
  duplicateMask,
  importMasks,
  moveMask,
  newMask,
  redoMasks,
  undoMasks,
  updateMask,
  useActiveMaskId,
} from '@/store/masks'
import { useActiveDocument, useSession } from '@/store/session'
import { selectShape, setActiveMask, setTool, useView } from '@/store/view'
import { mod } from '@/shell/TopBar'
import { pickPath } from '@/shell/PathDialog'

export function MasksPanel() {
  const doc = useActiveDocument()
  if (!doc) return <Empty>No document</Empty>
  return <MasksView doc={doc} />
}

function MasksView({ doc }: { doc: DocumentInfo }) {
  const info = useSession((s) => s.masks[doc.id])
  const masks = info?.masks ?? []
  const activeId = useActiveMaskId(doc.id)
  const active = masks.find((m) => m.id === activeId)
  const index = masks.findIndex((m) => m.id === activeId)

  return (
    <div className="flex h-full flex-col bg-panel" data-testid="masks-panel">
      <div className="flex h-9 shrink-0 items-center gap-0.5 border-b px-1.5">
        <IconButton label="New mask" onClick={() => void newMask(doc.id)} testId="mask-new">
          <Plus />
        </IconButton>
        <IconButton
          label="Duplicate"
          disabled={!active}
          onClick={() => active && void duplicateMask(doc.id, active.id)}
        >
          <Copy />
        </IconButton>
        <IconButton
          label="Delete mask"
          disabled={!active}
          onClick={() => active && void deleteMask(doc.id, active.id)}
          testId="mask-delete"
        >
          <Trash2 />
        </IconButton>
        <IconButton
          label="Move up"
          disabled={!active || index <= 0}
          onClick={() => active && void moveMask(doc.id, active.id, index - 1)}
        >
          <ArrowUp />
        </IconButton>
        <IconButton
          label="Move down"
          disabled={!active || index < 0 || index >= masks.length - 1}
          onClick={() => active && void moveMask(doc.id, active.id, index + 1)}
        >
          <ArrowDown />
        </IconButton>
        <span className="mx-1 h-5 w-px bg-border" />
        <IconButton
          label="Undo"
          shortcut={`${mod}Z`}
          disabled={!info?.can_undo}
          onClick={() => void undoMasks(doc.id)}
          testId="mask-undo"
        >
          <Undo2 />
        </IconButton>
        <IconButton
          label="Redo"
          shortcut={`${mod}⇧Z`}
          disabled={!info?.can_redo}
          onClick={() => void redoMasks(doc.id)}
          testId="mask-redo"
        >
          <Redo2 />
        </IconButton>
        <div className="ml-auto flex items-center gap-0.5">
          <ImportMenu doc={doc} />
          <ExportMenu doc={doc} disabled={!masks.length} />
        </div>
      </div>
      {info?.error && (
        <p className="border-b bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {info.error}
        </p>
      )}
      {masks.length === 0 ? (
        <Empty>
          <p>No masks yet.</p>
          <p className="max-w-64">
            Draw with the rectangle <Kbd>R</Kbd>, lasso <Kbd>L</Kbd> or polygon <Kbd>P</Kbd> tool,
            or import TIFFs or a mask set.
          </p>
          <Button size="sm" variant="outline" onClick={() => setTool('rect')}>
            Start drawing
          </Button>
        </Empty>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <MaskList doc={doc} masks={masks} activeId={activeId} />
          {active && <ShapeList doc={doc} mask={active} />}
        </ScrollArea>
      )}
    </div>
  )
}

function MaskList({
  doc,
  masks,
  activeId,
}: {
  doc: DocumentInfo
  masks: MaskInfo[]
  activeId: string | undefined
}) {
  const [renaming, setRenaming] = useState<string | null>(null)
  const [dragged, setDragged] = useState<string | null>(null)
  return (
    <div role="listbox" aria-label="Masks" className="flex flex-col py-1">
      <div className="grid grid-cols-[1.25rem_1.5rem_minmax(0,1fr)_4.5rem_5rem_3.5rem] gap-x-1 px-2 pb-1 text-[11px] text-muted-foreground">
        <span />
        <span />
        <span>Name</span>
        <span className="text-right">Pixels</span>
        <span className="text-right">Volume</span>
        <span className="text-right">%</span>
      </div>
      {masks.map((m, i) => (
        <div
          key={m.id}
          role="option"
          aria-selected={m.id === activeId}
          tabIndex={0}
          draggable={renaming !== m.id}
          onDragStart={() => setDragged(m.id)}
          onDragEnd={() => setDragged(null)}
          onDragOver={(e) => dragged && dragged !== m.id && e.preventDefault()}
          onDrop={() => dragged && void moveMask(doc.id, dragged, i)}
          onClick={() => setActiveMask(doc.id, m.id)}
          onKeyDown={(e) => {
            if (e.key === 'F2') setRenaming(m.id)
          }}
          className={cn(
            'grid cursor-default grid-cols-[1.25rem_1.5rem_minmax(0,1fr)_4.5rem_5rem_3.5rem] items-center gap-x-1 px-2 py-0.5 text-xs outline-none focus-visible:ring-1 focus-visible:ring-ring',
            m.id === activeId ? 'bg-accent' : 'hover:bg-accent/50',
            !m.visible && 'text-muted-foreground',
          )}
          data-testid="mask-row"
        >
          <label
            className="relative size-3.5 cursor-pointer overflow-hidden rounded-full border border-current/20"
            style={{ background: m.color }}
            title="Colour"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="color"
              value={m.color}
              aria-label={`Colour of ${m.name}`}
              className="absolute inset-0 size-full cursor-pointer appearance-none border-0 p-0 opacity-0"
              onChange={(e) => void updateMask(doc.id, m.id, { color: e.target.value })}
            />
          </label>
          <button
            type="button"
            className="rounded-sm p-0.5 hover:bg-muted"
            aria-label={m.visible ? `Hide ${m.name}` : `Show ${m.name}`}
            onClick={(e) => {
              e.stopPropagation()
              void updateMask(doc.id, m.id, { visible: !m.visible })
            }}
          >
            {m.visible ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />}
          </button>
          {renaming === m.id ? (
            <RenameField
              name={m.name}
              onDone={(name) => {
                setRenaming(null)
                if (name && name !== m.name) void updateMask(doc.id, m.id, { name })
              }}
            />
          ) : (
            <span
              className="truncate"
              title={`${m.name} (double click to rename)`}
              onDoubleClick={() => setRenaming(m.id)}
              data-testid="mask-name"
            >
              {m.name}
              {m.category && (
                <span className="ml-1.5 text-[10px] text-muted-foreground">{m.category}</span>
              )}
            </span>
          )}
          <span className="text-right font-mono tabular">{m.n_pixels ?? '–'}</span>
          <span className="text-right font-mono tabular">
            {m.volume == null ? '–' : formatValue(m.volume, 4)}
          </span>
          <span className="text-right font-mono tabular">
            {m.percent == null ? '–' : m.percent.toFixed(2)}
          </span>
        </div>
      ))}
    </div>
  )
}

function RenameField({ name, onDone }: { name: string; onDone: (name: string | null) => void }) {
  const [text, setText] = useState(name)
  return (
    <Input
      autoFocus
      value={text}
      aria-label="Mask name"
      className="h-6 px-1.5 text-xs"
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => onDone(text.trim())}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Enter') onDone(text.trim())
        if (e.key === 'Escape') onDone(null)
      }}
      onFocus={(e) => e.target.select()}
    />
  )
}

/** The operations of the active mask; selecting one shows its handles in the view. */
function ShapeList({ doc, mask }: { doc: DocumentInfo; mask: MaskInfo }) {
  const selected = useView((s) => s.selectedShape)
  const [category, setCategory] = useState<string | null>(null)
  return (
    <section className="border-t px-2 py-2" aria-label={`Shapes of ${mask.name}`}>
      <h3 className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
        Shapes of
        <span className="size-2 rounded-full" style={{ background: mask.color }} />
        <span className="truncate text-foreground">{mask.name}</span>
      </h3>
      {mask.operations.length === 0 && (
        <p className="py-1 text-xs text-muted-foreground">
          Empty. Shapes you draw are added to this mask.
        </p>
      )}
      <ul className="flex flex-col">
        {mask.operations.map((op: OperationInfo) => {
          const isSelected = selected?.opId === op.id
          return (
            <li
              key={op.id}
              className={cn(
                'group flex items-center gap-1.5 rounded-sm px-1 py-0.5 text-xs',
                isSelected ? 'bg-accent' : 'hover:bg-accent/50',
              )}
              onClick={() =>
                selectShape(isSelected ? null : { docId: doc.id, maskId: mask.id, opId: op.id })
              }
              data-testid="shape-row"
            >
              {op.mode === 'add' ? (
                <Plus className="size-3 shrink-0 text-success" aria-label="adds" />
              ) : (
                <Minus className="size-3 shrink-0 text-destructive" aria-label="subtracts" />
              )}
              <span className="min-w-0 flex-1 truncate">
                {describeShape(op.shape as Parameters<typeof describeShape>[0])}
              </span>
              <button
                type="button"
                aria-label="Delete shape"
                className="rounded-sm p-0.5 opacity-0 group-hover:opacity-100 hover:bg-muted focus-visible:opacity-100"
                onClick={(e) => {
                  e.stopPropagation()
                  void deleteShape(doc.id, mask.id, op.id)
                }}
              >
                <Trash2 className="size-3" />
              </button>
            </li>
          )
        })}
      </ul>
      {/* roadmap hook (spec §11): regions carry a compound class */}
      <label className="mt-2 flex items-center gap-2 text-[11px] text-muted-foreground">
        Category
        <Input
          value={category ?? mask.category ?? ''}
          placeholder="e.g. monoaromatics"
          className="h-6 flex-1 px-1.5 text-xs"
          onChange={(e) => setCategory(e.target.value)}
          onBlur={() => {
            if (category !== null && category !== (mask.category ?? '')) {
              void updateMask(doc.id, mask.id, { category })
            }
            setCategory(null)
          }}
          onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
        />
      </label>
    </section>
  )
}

function ImportMenu({ doc }: { doc: DocumentInfo }) {
  const [replace, setReplace] = useState(false)
  const documents = useSession((s) => s.documents)
  const order = useSession((s) => s.order)
  const others = order.filter((id) => id !== doc.id && documents[id])

  const fromFile = async (mode: 'any' | 'folder') => {
    const path = await pickPath(
      mode === 'folder'
        ? {
            title: 'Import a folder of mask TIFFs',
            description:
              'Every .tif in the folder becomes a mask (as the old mask creator saved them).',
            mode: 'folder',
            confirm: 'Import folder',
          }
        : {
            title: 'Import masks',
            description: 'A mask set (.gcxgc-masks.json) or a mask TIFF.',
            mode: 'file',
            extensions: ['.json', '.tif', '.tiff'],
            confirm: 'Import',
          },
    )
    if (path) await importMasks(doc.id, { path, replace })
  }

  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Import masks"
              data-testid="mask-import"
            >
              <FolderInput />
            </Button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent>Import masks</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>Import masks</DropdownMenuLabel>
        <DropdownMenuItem onSelect={() => void fromFile('any')}>
          Mask set or TIFF file…
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void fromFile('folder')}>
          Folder of TIFFs…
        </DropdownMenuItem>
        {others.length > 0 && <DropdownMenuSeparator />}
        {others.map((id) => (
          <DropdownMenuItem
            key={id}
            onSelect={() => void importMasks(doc.id, { from_document: id, replace }, 'Copy masks')}
          >
            <span className="truncate">From {documents[id].name}</span>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuCheckboxItem
          checked={replace}
          onCheckedChange={setReplace}
          onSelect={(e) => e.preventDefault()}
        >
          Replace the current masks
        </DropdownMenuCheckboxItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function ExportMenu({ doc, disabled }: { doc: DocumentInfo; disabled: boolean }) {
  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Export masks"
              disabled={disabled}
              data-testid="mask-export"
            >
              <Download />
            </Button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent>Export masks</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel>Export</DropdownMenuLabel>
        <DropdownMenuItem
          onSelect={() =>
            void downloadExport(doc.id, { what: 'mask_set', format: 'json' }, 'Export mask set')
          }
        >
          Mask set (.gcxgc-masks.json)
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() =>
            void downloadExport(doc.id, { what: 'masks', format: 'tiff' }, 'Export TIFFs')
          }
        >
          Masks as TIFF files (.zip)
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() =>
            void downloadExport(
              doc.id,
              { what: 'integration', format: 'csv' },
              'Export integration',
            )
          }
        >
          Integration results (.csv)
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function IconButton({
  label,
  shortcut,
  disabled,
  onClick,
  testId,
  children,
}: {
  label: string
  shortcut?: string
  disabled?: boolean
  onClick: () => void
  testId?: string
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={label}
            disabled={disabled}
            onClick={onClick}
            data-testid={testId}
          >
            {children}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>
        {label}
        {shortcut && <Kbd>{shortcut}</Kbd>}
      </TooltipContent>
    </Tooltip>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-xs text-muted-foreground">
      {children}
    </div>
  )
}
