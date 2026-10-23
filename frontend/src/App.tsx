import { Loader2, PlugZap } from 'lucide-react'

import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { CommandPalette } from '@/shell/CommandPalette'
import { OpenDialog } from '@/shell/OpenDialog'
import { SettingsDialog } from '@/shell/SettingsDialog'
import { useShortcuts } from '@/shell/shortcuts'
import { StatusBar } from '@/shell/StatusBar'
import { useApplyTheme } from '@/shell/theme'
import { TopBar } from '@/shell/TopBar'
import { Welcome } from '@/shell/Welcome'
import { Workspace } from '@/shell/Workspace'
import { useSession } from '@/store/session'

export function App() {
  useApplyTheme()
  useShortcuts()
  const ready = useSession((s) => s.ready)
  const fatal = useSession((s) => s.fatal)
  const kind = useSession((s) => (s.activeId ? s.documents[s.activeId]?.kind : undefined))

  let main: React.ReactNode
  if (fatal) main = <Fatal message={fatal} />
  else if (!ready)
    main = (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" /> Connecting to the backend…
      </div>
    )
  else if (kind) main = <Workspace kind={kind} />
  else main = <Welcome />

  return (
    <TooltipProvider delayDuration={500}>
      <div className="flex h-full flex-col">
        <TopBar />
        <main className="min-h-0 flex-1">{main}</main>
        <StatusBar />
      </div>
      <OpenDialog />
      <SettingsDialog />
      <CommandPalette />
      <Toaster position="bottom-right" closeButton />
    </TooltipProvider>
  )
}

function Fatal({ message }: { message: string }) {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="flex max-w-lg flex-col gap-2 rounded-xl border bg-card p-6 text-sm">
        <h1 className="flex items-center gap-2 font-semibold">
          <PlugZap className="size-4 text-destructive" /> Cannot reach the pyGCxGC backend
        </h1>
        <p className="text-muted-foreground">{message}</p>
        <p className="text-muted-foreground">
          Start it with <code className="font-mono text-foreground">python -m pyGCxGC.app</code> and
          open the URL it prints (it contains the access token).
        </p>
      </div>
    </div>
  )
}
