/**
 * Folder listing of the built-in file browser (spec SHELL-5, browser and
 * notebook mode), backed by `/api/fs`. Used by the open dialog and the path
 * picker.
 */

import { ArrowUp, File, FileChartLine, Folder, Home } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { FsEntry, FsListing } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { cn, formatBytes } from '@/lib/utils'
import { api } from '@/store/session'

interface Props {
  /** Folder to start in (falls back to the home folder). */
  start?: string
  /** Files worth showing; the others appear with "Show all files". */
  isRelevant: (name: string) => boolean
  emptyText: string
  /** The selected path, or a predicate for a multiple selection. */
  selected: string | null | ((path: string) => boolean)
  onSelect: (entry: FsEntry) => void
  /** Double click on a file (or, with `selectFolders`, on nothing else). */
  onActivate?: (entry: FsEntry) => void
  /** A click selects folders too; a double click opens them. */
  selectFolders?: boolean
  onFolderChange?: (path: string) => void
}

export function FileBrowser({
  start,
  isRelevant,
  emptyText,
  selected,
  onSelect,
  onActivate,
  selectFolders = false,
  onFolderChange,
}: Props) {
  const [listing, setListing] = useState<FsListing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pathText, setPathText] = useState('')
  const [allFiles, setAllFiles] = useState(false)

  // a listing does not overwrite a path being typed
  const editing = useRef(false)
  const show = (result: FsListing) => {
    setListing(result)
    if (!editing.current) setPathText(result.path)
    setError(null)
    onFolderChange?.(result.path)
  }
  const browse = (path?: string) =>
    api()
      .listDir(path)
      .then(show)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))

  useEffect(() => {
    let cancelled = false
    api()
      .listDir(start)
      .catch(() => api().listDir())
      .then((result) => !cancelled && show(result))
      .catch((err: unknown) => !cancelled && setError(String(err)))
    return () => {
      cancelled = true
    }
    // only on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const entries = useMemo(
    () => (listing?.entries ?? []).filter((e) => e.is_dir || allFiles || isRelevant(e.name)),
    [listing, allFiles, isRelevant],
  )

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <form
        className="flex gap-1"
        onSubmit={(e) => {
          e.preventDefault()
          editing.current = false
          void browse(pathText)
        }}
      >
        <Button
          type="button"
          variant="outline"
          size="icon-sm"
          aria-label="Home folder"
          onClick={() => {
            editing.current = false
            void browse()
          }}
        >
          <Home />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="icon-sm"
          aria-label="Parent folder"
          disabled={!listing?.parent}
          onClick={() => {
            editing.current = false
            if (listing?.parent) void browse(listing.parent)
          }}
        >
          <ArrowUp />
        </Button>
        <Input
          value={pathText}
          onChange={(e) => {
            editing.current = true
            setPathText(e.target.value)
          }}
          className="h-7 font-mono text-xs"
          aria-label="Folder"
        />
      </form>
      <ScrollArea className="h-72 rounded-md border">
        <div className="flex flex-col p-1" role="listbox" aria-label="Files">
          {error && <p className="p-2 text-sm text-destructive">{error}</p>}
          {entries.map((e) => {
            const isSelected =
              typeof selected === 'function' ? selected(e.path) : selected === e.path
            const relevant = e.is_dir || isRelevant(e.name)
            return (
              <button
                key={e.path}
                type="button"
                role="option"
                aria-selected={isSelected}
                className={cn(
                  'flex items-center gap-2 rounded-sm px-2 py-1 text-left text-sm hover:bg-accent',
                  isSelected && 'bg-accent',
                  !relevant && 'text-muted-foreground',
                )}
                onClick={() => {
                  editing.current = false
                  if (e.is_dir && !selectFolders) void browse(e.path)
                  else onSelect(e)
                }}
                onDoubleClick={() => {
                  editing.current = false
                  if (e.is_dir) void browse(e.path)
                  else onActivate?.(e)
                }}
              >
                {e.is_dir ? (
                  <Folder className="size-4 shrink-0 text-primary" />
                ) : relevant ? (
                  <FileChartLine className="size-4 shrink-0" />
                ) : (
                  <File className="size-4 shrink-0" />
                )}
                <span className="min-w-0 flex-1 truncate">{e.name}</span>
                {!e.is_dir && (
                  <span className="shrink-0 font-mono text-xs text-muted-foreground tabular">
                    {formatBytes(e.size)}
                  </span>
                )}
              </button>
            )
          })}
          {listing && entries.length === 0 && (
            <p className="p-2 text-sm text-muted-foreground">{emptyText}</p>
          )}
        </div>
      </ScrollArea>
      <label className="flex items-center gap-2 self-start text-xs text-muted-foreground">
        <Switch checked={allFiles} onCheckedChange={setAllFiles} size="sm" />
        Show all files
      </label>
    </div>
  )
}
