/**
 * Status bar (spec SHELL-3): cursor readout, running jobs with progress and
 * cancel, the active library and a data summary.
 */

import { BookMarked, CircleAlert, Loader2, X } from 'lucide-react'

import { Progress } from '@/components/ui/progress'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { JobInfo } from '@/api/types'
import { formatValue } from '@/lib/ticks'
import { cn } from '@/lib/utils'
import { cancelJob, useActiveDocument, useSession } from '@/store/session'
import { useView } from '@/store/view'

export function StatusBar() {
  const doc = useActiveDocument()
  const connection = useSession((s) => s.connection)
  return (
    <footer
      className="flex h-6 shrink-0 items-center gap-3 border-t bg-chrome px-2 text-[11px] text-muted-foreground"
      data-testid="status-bar"
    >
      <CursorReadout />
      <div className="ml-auto flex min-w-0 items-center gap-3">
        <Jobs />
        <ActiveLibrary />
        {doc && <span className="truncate tabular">{doc.summary}</span>}
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className={cn(
                'size-2 shrink-0 rounded-full',
                connection === 'open'
                  ? 'bg-success'
                  : connection === 'connecting'
                    ? 'bg-warning'
                    : 'bg-destructive',
              )}
              aria-label={`Backend ${connection}`}
            />
          </TooltipTrigger>
          <TooltipContent>
            {connection === 'open'
              ? 'Connected to the pyGCxGC backend'
              : connection === 'connecting'
                ? 'Connecting…'
                : 'Backend disconnected, retrying'}
          </TooltipContent>
        </Tooltip>
      </div>
    </footer>
  )
}

function CursorReadout() {
  const cursor = useView((s) => s.cursor)
  if (!cursor) return <span className="opacity-70">–</span>
  return (
    <div className="flex min-w-0 items-center gap-3 font-mono tabular" data-testid="cursor-readout">
      <span>
        <span className="opacity-70">RT1 </span>
        {cursor.rt1.toFixed(3)} min
      </span>
      <span>
        <span className="opacity-70">RT2 </span>
        {cursor.rt2.toFixed(3)} s
      </span>
      <span className="text-foreground">
        <span className="text-muted-foreground opacity-70">I </span>
        {cursor.value == null ? '–' : formatValue(cursor.value, 4)}
        {cursor.pooled && <span className="text-muted-foreground"> (max)</span>}
      </span>
      {cursor.scan != null && (
        <span>
          <span className="opacity-70">scan </span>
          {cursor.scan}
        </span>
      )}
      <span>
        <span className="opacity-70">px </span>
        {cursor.row}, {cursor.col}
      </span>
    </div>
  )
}

function Jobs() {
  const jobs = useSession((s) => s.jobs)
  const list = Object.values(jobs)
  if (!list.length) return null
  return (
    <div className="flex items-center gap-3" data-testid="jobs">
      {list.map((job) => (
        <JobItem key={job.id} job={job} />
      ))}
    </div>
  )
}

function JobItem({ job }: { job: JobInfo }) {
  const running = job.status === 'running' || job.status === 'queued'
  return (
    <div className="flex items-center gap-1.5">
      {running ? (
        <Loader2 className="size-3 animate-spin" />
      ) : job.status === 'failed' ? (
        <CircleAlert className="size-3 text-destructive" />
      ) : null}
      <span className="max-w-48 truncate">
        {job.label}
        {job.message && running ? ` · ${job.message}` : ''}
        {!running && job.status !== 'done' ? ` · ${job.status}` : ''}
      </span>
      {running && (
        <>
          <Progress value={job.progress * 100} className="h-1 w-16" />
          <button
            type="button"
            className="rounded-sm p-0.5 hover:bg-accent hover:text-foreground"
            aria-label={`Cancel ${job.label}`}
            onClick={() => void cancelJob(job.id)}
          >
            <X className="size-3" />
          </button>
        </>
      )}
    </div>
  )
}

function ActiveLibrary() {
  const libraries = useSession((s) => s.libraries)
  const active = Object.values(libraries).filter((l) => l.active)
  if (!active.length) return null
  return (
    <span className="flex items-center gap-1 truncate">
      <BookMarked className="size-3" />
      {active.map((l) => `${l.name}${l.status === 'loading' ? ' (loading)' : ''}`).join(', ')}
    </span>
  )
}
