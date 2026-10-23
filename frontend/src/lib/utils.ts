export { cn } from 'cn'

/** Call `fn` at most once per animation frame with the latest arguments. */
export function rafThrottle<A extends unknown[]>(fn: (...args: A) => void) {
  let frame = 0
  let last: A
  const throttled = (...args: A) => {
    last = args
    if (!frame) {
      frame = requestAnimationFrame(() => {
        frame = 0
        fn(...last)
      })
    }
  }
  throttled.cancel = () => {
    if (frame) cancelAnimationFrame(frame)
    frame = 0
  }
  return throttled
}

export function debounce<A extends unknown[]>(fn: (...args: A) => void, ms: number) {
  let timer: ReturnType<typeof setTimeout> | undefined
  const debounced = (...args: A) => {
    clearTimeout(timer)
    timer = setTimeout(() => fn(...args), ms)
  }
  debounced.cancel = () => clearTimeout(timer)
  return debounced
}

export function basename(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path
}

export function dirname(path: string): string {
  const i = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return i > 0 ? path.slice(0, i) : path
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let u = 0
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024
    u++
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[u]}`
}

export function isEditableTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el) return false
  return (
    el.isContentEditable ||
    el.tagName === 'INPUT' ||
    el.tagName === 'TEXTAREA' ||
    el.tagName === 'SELECT' ||
    el.getAttribute('role') === 'combobox'
  )
}
