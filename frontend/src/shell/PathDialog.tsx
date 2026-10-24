/**
 * Pick a file or folder with the built-in browser (browser/notebook mode;
 * the desktop app will use native dialogs, spec SHELL-5).
 *
 *     const path = await pickPath({ title: 'Import masks', mode: 'any', extensions: ['.tif'] })
 *     const paths = await pickPaths({ title: 'Batch', extensions: ['.csv'] })
 *     const file = await pickPath({ title: 'Save', mode: 'save', defaultName: 'a.gcxgc' })
 */

import { useState } from 'react'
import { create } from 'zustand'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { basename } from '@/lib/utils'
import { updateSettings, useSession } from '@/store/session'

import { FileBrowser } from './FileBrowser'

export interface PickOptions {
  title: string
  description?: string
  /** `folder`: folders only; `any`: a file or a folder; `save`: a folder and a file name. */
  mode: 'file' | 'folder' | 'any' | 'save'
  extensions?: string[]
  confirm?: string
  /** File name proposed in `save` mode. */
  defaultName?: string
}

interface Request extends PickOptions {
  multiple: boolean
  resolve: (paths: string[] | null) => void
}

const usePathDialog = create<{ request: Request | null }>(() => ({ request: null }))

export function pickPath(options: PickOptions): Promise<string | null> {
  return open(options, false).then((paths) => paths?.[0] ?? null)
}

/** Several files (click toggles a file). */
export function pickPaths(options: Omit<PickOptions, 'mode'>): Promise<string[] | null> {
  return open({ ...options, mode: 'file' }, true)
}

function open(options: PickOptions, multiple: boolean): Promise<string[] | null> {
  return new Promise((resolve) => {
    usePathDialog.getState().request?.resolve(null)
    usePathDialog.setState({ request: { ...options, multiple, resolve } })
  })
}

function finish(paths: string[] | null) {
  const request = usePathDialog.getState().request
  usePathDialog.setState({ request: null })
  request?.resolve(paths)
}

export function PathDialog() {
  const request = usePathDialog((s) => s.request)
  return (
    <Dialog open={request !== null} onOpenChange={(o) => !o && finish(null)}>
      {request && <PathDialogBody key={request.title} request={request} />}
    </Dialog>
  )
}

function PathDialogBody({ request }: { request: Request }) {
  const start = useSession((s) => s.settings.ui?.last_folder)
  const [folder, setFolder] = useState<string | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const [many, setMany] = useState<string[]>([])
  const [name, setName] = useState(request.defaultName ?? '')
  const saving = request.mode === 'save'
  const extensions = request.extensions?.map((e) => e.toLowerCase()) ?? []
  const isRelevant = (name: string) =>
    !extensions.length || extensions.some((ext) => name.toLowerCase().endsWith(ext))
  // in folder mode the current folder is the default choice
  const selected = saving
    ? folder && name.trim()
      ? joinPath(folder, name.trim())
      : null
    : (picked ?? (request.mode === 'folder' ? folder : null))
  const result = request.multiple ? many : selected ? [selected] : []

  const confirm = (paths: string[] = result) => {
    if (!paths.length) return
    if (folder) void updateSettings({ ui: { last_folder: folder } })
    finish(paths)
  }

  return (
    <DialogContent
      className="flex max-h-[85vh] flex-col gap-3 sm:max-w-2xl"
      data-testid="path-dialog"
    >
      <DialogHeader>
        <DialogTitle>{request.title}</DialogTitle>
        {request.description && <DialogDescription>{request.description}</DialogDescription>}
      </DialogHeader>
      <FileBrowser
        start={start}
        isRelevant={isRelevant}
        emptyText={request.mode === 'folder' ? 'No subfolders.' : 'No matching files here.'}
        selected={request.multiple ? (path) => many.includes(path) : selected}
        selectFolders={request.mode !== 'file' && !saving}
        onSelect={(e) => {
          if (saving) {
            if (!e.is_dir) setName(basename(e.path))
            return
          }
          if (request.mode === 'folder' && !e.is_dir) return
          if (request.multiple) {
            setMany((m) => (m.includes(e.path) ? m.filter((p) => p !== e.path) : [...m, e.path]))
          } else setPicked(e.path)
        }}
        onActivate={(e) =>
          !request.multiple && request.mode !== 'folder' && !saving && confirm([e.path])
        }
        onFolderChange={(path) => {
          setFolder(path)
          setPicked(null)
        }}
      />
      {saving && (
        <label className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">File name</span>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && confirm()}
            className="h-8 flex-1 font-mono text-xs"
            autoFocus
            data-testid="path-name"
          />
        </label>
      )}
      <p className="min-h-5 truncate rounded-md bg-muted/60 px-3 py-2 font-mono text-xs">
        {request.multiple
          ? many.length
            ? `${many.length} file${many.length > 1 ? 's' : ''}: ${many.map(basename).join(', ')}`
            : 'Click files to select them (also across folders)'
          : (selected ?? 'Nothing selected')}
      </p>
      <DialogFooter>
        <Button variant="ghost" onClick={() => finish(null)}>
          Cancel
        </Button>
        <Button disabled={!result.length} onClick={() => confirm()} data-testid="path-confirm">
          {request.confirm ?? 'Select'}
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}

/** `folder` + `name` with the folder's separator. */
export function joinPath(folder: string, name: string): string {
  const sep = folder.includes('\\') && !folder.includes('/') ? '\\' : '/'
  return folder.endsWith(sep) ? folder + name : folder + sep + name
}
