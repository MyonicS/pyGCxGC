import { useLayoutEffect, useRef, useState } from 'react'

/** Size (CSS px) of an element, updated on resize. */
export function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const update = (width: number, height: number) =>
      setSize((s) =>
        s.width === Math.round(width) && s.height === Math.round(height)
          ? s
          : { width: Math.round(width), height: Math.round(height) },
      )
    const rect = el.getBoundingClientRect()
    update(rect.width, rect.height)
    const observer = new ResizeObserver(([entry]) => {
      update(entry.contentRect.width, entry.contentRect.height)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, size] as const
}
