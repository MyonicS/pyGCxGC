import { Construction } from 'lucide-react'

/** A panel of a later milestone (spec §14); it reserves its place in the layout. */
export function PlaceholderPanel({
  milestone,
  children,
}: {
  milestone: number
  children: React.ReactNode
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 bg-panel p-4 text-center text-xs text-muted-foreground">
      <Construction className="size-5 opacity-60" />
      <p className="max-w-56">{children}</p>
      <p className="opacity-80">Coming in milestone {milestone}.</p>
    </div>
  )
}
