/**
 * Library switcher (spec LIB-2/3) for the top bar and the Match panel.
 * Checking a library searches it too (several can be active); *only*
 * switches to that library alone. Switching re-searches the current spectrum
 * (in the backend) and marks peak tables stale.
 */

import { BookMarked, ChevronDown, Loader2 } from 'lucide-react'

import type { LibraryInfo } from '@/api/types'
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
import { cn } from '@/lib/utils'
import { showPanel } from '@/shell/Workspace'
import { toggleLibrary } from '@/store/ms'
import { setLibraryActive, useSession } from '@/store/session'

export function activeLibraryLabel(libraries: LibraryInfo[]): string {
  const active = libraries.filter((l) => l.active)
  if (active.length === 0) return 'No library'
  if (active.length === 1) return active[0].name
  return `${active.length} libraries`
}

export function LibraryPicker({ className }: { className?: string }) {
  const libraries = useSession((s) => s.libraries)
  const list = Object.values(libraries)
  const loading = list.some((l) => l.status === 'loading')
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={cn('h-7 max-w-56 gap-1.5 px-2 text-xs font-normal', className)}
          aria-label="Active library"
          data-testid="library-picker"
        >
          {loading ? <Loader2 className="animate-spin" /> : <BookMarked />}
          <span className="truncate">{activeLibraryLabel(list)}</span>
          <ChevronDown className="opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-60">
        <DropdownMenuLabel className="text-xs text-muted-foreground">Search with</DropdownMenuLabel>
        {list.map((lib) => (
          <DropdownMenuCheckboxItem
            key={lib.id}
            checked={lib.active}
            disabled={lib.status !== 'ready'}
            onSelect={(e) => {
              e.preventDefault() // keep the menu open to check several
              void toggleLibrary(lib.id, !lib.active)
            }}
            className="group pr-14"
          >
            <span className="truncate">{lib.name}</span>
            <span className="ml-auto text-xs text-muted-foreground group-hover:hidden">
              {lib.status === 'ready' ? (lib.n_spectra?.toLocaleString() ?? '') : lib.status}
            </span>
            {lib.status === 'ready' && (
              <button
                type="button"
                className="absolute right-2 hidden rounded px-1.5 text-xs text-primary group-hover:block hover:bg-background"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation()
                  void setLibraryActive(lib.id, true)
                }}
                aria-label={`Only ${lib.name}`}
              >
                only
              </button>
            )}
          </DropdownMenuCheckboxItem>
        ))}
        {list.length > 0 && <DropdownMenuSeparator />}
        <DropdownMenuItem onSelect={() => showPanel('libraries')}>
          Manage libraries…
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
