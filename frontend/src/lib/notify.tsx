/**
 * Non-blocking notifications (spec SHELL-4): errors are toasts with a
 * "Details" expander showing the Python traceback. No modal error dialogs.
 */

import { toast } from 'sonner'

import { ApiError } from '@/api/transport'

function Details({
  message,
  traceback,
  expanded,
  onToggle,
}: {
  message: string
  traceback: string | null
  expanded: boolean
  onToggle: () => void
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="break-words">{message}</span>
      {traceback ? (
        <>
          <button
            type="button"
            className="self-start text-xs underline underline-offset-2 opacity-80 hover:opacity-100"
            onClick={onToggle}
          >
            {expanded ? 'Hide details' : 'Details'}
          </button>
          {expanded && (
            <pre className="max-h-56 overflow-auto rounded-sm bg-muted p-2 font-mono text-[11px] leading-snug whitespace-pre text-muted-foreground select-text">
              {traceback}
            </pre>
          )}
        </>
      ) : null}
    </div>
  )
}

let nextId = 0

/**
 * (Re)show an error toast. Toggling the details re-issues the toast under the
 * same id, so sonner measures its new height; an expanded toast stays open.
 */
function showError(
  id: string,
  title: string,
  message: string,
  tb: string | null,
  expanded: boolean,
) {
  toast.error(title, {
    id,
    description: (
      <Details
        message={message}
        traceback={tb}
        expanded={expanded}
        onToggle={() => showError(id, title, message, tb, !expanded)}
      />
    ),
    duration: expanded ? Infinity : tb ? 12000 : 8000,
  })
}

/** Show an error. `error` may be an {@link ApiError}, an `Error` or a message. */
export function notifyError(title: string, error: unknown, traceback?: string | null) {
  if (error instanceof DOMException && error.name === 'AbortError') return
  let message: string
  let tb = traceback ?? null
  if (error instanceof ApiError) {
    message = error.message
    tb = tb ?? error.traceback
  } else if (error instanceof Error) {
    message = error.message
    tb = tb ?? error.stack ?? null
  } else {
    message = String(error)
  }
  showError(`error-${nextId++}`, title, message, tb, false)
}

export function notifyWarnings(title: string, warnings: string[]) {
  toast.warning(title, {
    description: (
      <ul className="list-disc pl-4">
        {warnings.map((w, i) => (
          <li key={i}>{w}</li>
        ))}
      </ul>
    ),
    duration: 10000,
  })
}

export function notifyInfo(title: string, description?: string) {
  toast.info(title, { description })
}
