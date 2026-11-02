/**
 * Centred progress card while files or projects open (the status bar's job
 * list is easy to miss). The scrim only dims: the workspace stays usable. It
 * appears after a short delay, so quick opens do not flash it.
 */

import { Loader2, X } from 'lucide-react'

import type { JobInfo } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { cancelJob, useSession } from '@/store/session'

/** Job kinds that get the card. */
const KINDS = new Set(['open', 'project'])

export function BusyOverlay() {
  const jobs = useSession((s) => s.jobs)
  const busy = Object.values(jobs).filter(
    (j) => KINDS.has(j.kind) && (j.status === 'running' || j.status === 'queued'),
  )
  if (!busy.length) return null
  return (
    <div
      className="busy-in pointer-events-none absolute inset-0 z-40 flex items-center justify-center bg-background/60 p-6"
      data-testid="busy-overlay"
    >
      <div
        className="pointer-events-auto flex w-96 max-w-full flex-col gap-4 rounded-xl border bg-card p-5 shadow-lg"
        role="status"
        aria-live="polite"
      >
        {busy.map((job) => (
          <BusyJob key={job.id} job={job} />
        ))}
      </div>
    </div>
  )
}

function BusyJob({ job }: { job: JobInfo }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Loader2 className="size-5 shrink-0 animate-spin text-primary" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={job.label}>
          {job.label}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={`Cancel ${job.label}`}
          onClick={() => void cancelJob(job.id)}
        >
          <X />
        </Button>
      </div>
      {job.progress > 0 ? (
        <Progress value={job.progress * 100} className="h-1.5" />
      ) : (
        <div className="relative h-1.5 overflow-hidden rounded-full bg-muted">
          <div className="busy-indeterminate absolute inset-y-0 w-1/3 rounded-full bg-primary" />
        </div>
      )}
      {job.message && <p className="truncate text-xs text-muted-foreground">{job.message}</p>}
    </div>
  )
}
