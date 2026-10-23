/** Empty state (spec SHELL-5): open a file, recent files, drag and drop. */

import { Clock, FolderOpen, Upload } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/Kbd'
import { basename, cn, dirname } from '@/lib/utils'
import { useSession } from '@/store/session'

import { droppedFiles } from './dropFiles'
import { openPath, showOpenDialog } from './openFile'
import { mod } from './TopBar'

const NO_FILES: string[] = []

export function Welcome() {
  const recent = useSession((s) => s.settings.recent_files ?? NO_FILES)
  const version = useSession((s) => s.version)
  const [over, setOver] = useState(false)
  return (
    <div
      className="flex h-full items-center justify-center overflow-auto bg-background p-8"
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        droppedFiles(e.dataTransfer)
      }}
      data-testid="welcome"
    >
      <div
        className={cn(
          'flex w-full max-w-xl flex-col gap-6 rounded-xl border bg-card p-8 shadow-sm transition-colors',
          over && 'border-primary bg-primary/5',
        )}
      >
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold tracking-tight">pyGCxGC Studio</h1>
          <p className="text-sm text-muted-foreground">
            GCxGC-FID mask integration and GCxGC-MS inspection. Open an OpenChrom CSV, ANDI-MS .cdf
            or JCAMP-DX file.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={() => showOpenDialog()} data-testid="welcome-open">
            <FolderOpen />
            Open file…
          </Button>
          <span className="text-xs text-muted-foreground">
            <Kbd className="ml-0">{mod}O</Kbd> · palette <Kbd>{mod}K</Kbd>
          </span>
        </div>
        <div className="flex flex-col gap-2">
          <h2 className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Clock className="size-3.5" /> Recent files
          </h2>
          {recent.length === 0 ? (
            <p className="text-sm text-muted-foreground">No recent files yet.</p>
          ) : (
            <ul className="flex flex-col">
              {recent.map((path) => (
                <li key={path}>
                  <button
                    type="button"
                    className="flex w-full items-baseline gap-2 rounded-md px-2 py-1.5 text-left hover:bg-accent"
                    onClick={() => void openPath(path)}
                    title={path}
                  >
                    <span className="truncate text-sm font-medium">{basename(path)}</span>
                    <span className="min-w-0 flex-1 truncate text-right text-xs text-muted-foreground">
                      {dirname(path)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
          <Upload className="size-4" />
          Drop a file here (desktop app)
        </div>
        {version && <p className="text-[11px] text-muted-foreground">pyGCxGC {version}</p>}
      </div>
    </div>
  )
}
