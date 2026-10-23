/**
 * dockview workspace (spec SHELL-2). The layout depends on the kind of the
 * active document (FID or MS), persists per kind in the settings, and
 * *Reset layout* restores the defaults.
 */

import {
  DockviewDefaultTab,
  DockviewReact,
  type DockviewApi,
  type DockviewReadyEvent,
  type DockviewTheme,
  type IDockviewPanelHeaderProps,
  type IDockviewPanelProps,
} from 'dockview-react'
import { useEffect, useMemo, useRef } from 'react'

import type { DocumentKind } from '@/api/types'
import { ChromatogramPanel } from '@/chromatogram/ChromatogramPanel'
import { debounce } from '@/lib/utils'
import { IntegrationPanel } from '@/panels/IntegrationPanel'
import { MasksPanel } from '@/panels/MasksPanel'
import { PlaceholderPanel } from '@/panels/PlaceholderPanel'
import { ProcessingPanel } from '@/panels/ProcessingPanel'
import { updateSettings, useSession } from '@/store/session'

/** Bump when the panels or default layouts change: saved layouts are dropped. */
const LAYOUT_VERSION = 2

const theme: DockviewTheme = {
  name: 'pygcxgc',
  className: 'dockview-theme-pygcxgc',
  gap: 0,
  dndOverlayMounting: 'absolute',
}

export const PANELS = {
  chromatogram: { title: 'Chromatogram', component: () => <ChromatogramPanel /> },
  processing: { title: 'Processing', component: () => <ProcessingPanel /> },
  masks: { title: 'Masks', component: () => <MasksPanel /> },
  integration: { title: 'Integration', component: () => <IntegrationPanel /> },
  spectrum: {
    title: 'Spectrum',
    component: () => (
      <PlaceholderPanel milestone={3}>Mass spectrum of the selected point.</PlaceholderPanel>
    ),
  },
  match: {
    title: 'Match',
    component: () => (
      <PlaceholderPanel milestone={3}>
        Head-to-tail plot and structure of the selected hit.
      </PlaceholderPanel>
    ),
  },
  hits: {
    title: 'Hits',
    component: () => (
      <PlaceholderPanel milestone={3}>Library hits of the selected point.</PlaceholderPanel>
    ),
  },
  peaks: {
    title: 'Peaks',
    component: () => <PlaceholderPanel milestone={3}>Peak table with identities.</PlaceholderPanel>,
  },
  libraries: {
    title: 'Libraries',
    component: () => <PlaceholderPanel milestone={3}>Spectral libraries.</PlaceholderPanel>,
  },
} satisfies Record<string, { title: string; component: React.FunctionComponent }>

export type PanelId = keyof typeof PANELS

export const LAYOUT_PANELS: Record<DocumentKind, PanelId[]> = {
  fid: ['chromatogram', 'masks', 'processing', 'integration'],
  ms: [
    'chromatogram',
    'spectrum',
    'processing',
    'match',
    'hits',
    'peaks',
    'libraries',
    'masks',
    'integration',
  ],
}

const components = Object.fromEntries(
  Object.entries(PANELS).map(([id, p]) => {
    const Component = p.component
    const Panel = (_props: IDockviewPanelProps) => <Component />
    Panel.displayName = `${id}Panel`
    return [id, Panel]
  }),
) as Record<PanelId, React.FunctionComponent<IDockviewPanelProps>>

/** The chromatogram cannot be closed: it is the workspace's anchor. */
const tabComponents = {
  fixed: (props: IDockviewPanelHeaderProps) => <DockviewDefaultTab {...props} hideClose />,
}

function add(
  api: DockviewApi,
  id: PanelId,
  position?: Parameters<DockviewApi['addPanel']>[0]['position'],
) {
  return api.addPanel({
    id,
    component: id,
    title: PANELS[id].title,
    position,
    tabComponent: id === 'chromatogram' ? 'fixed' : undefined,
  })
}

/** Default layouts (spec §6 and §7). */
export function buildDefaultLayout(api: DockviewApi, kind: DocumentKind) {
  api.clear()
  const width = api.width || 1200
  const height = api.height || 800
  add(api, 'chromatogram')
  if (kind === 'fid') {
    // chromatogram ~70 %; Masks (with Processing as a tab) over Integration
    const masks = add(api, 'masks', { referencePanel: 'chromatogram', direction: 'right' })
    add(api, 'processing', { referencePanel: 'masks', direction: 'within' })
    add(api, 'integration', { referencePanel: 'masks', direction: 'below' })
    masks.api.setActive()
    masks.group.api.setSize({ width: Math.round(width * 0.3) })
  } else {
    // chromatogram 2/3; Spectrum (+ Processing), Match, then Hits / Peaks / Libraries
    const spectrum = add(api, 'spectrum', { referencePanel: 'chromatogram', direction: 'right' })
    add(api, 'processing', { referencePanel: 'spectrum', direction: 'within' })
    const match = add(api, 'match', { referencePanel: 'spectrum', direction: 'below' })
    const hits = add(api, 'hits', { referencePanel: 'match', direction: 'below' })
    add(api, 'peaks', { referencePanel: 'hits', direction: 'within' })
    add(api, 'libraries', { referencePanel: 'hits', direction: 'within' })
    // masks work on MS documents too (spec MASK-8)
    add(api, 'masks', { referencePanel: 'hits', direction: 'within' })
    add(api, 'integration', { referencePanel: 'hits', direction: 'within' })
    spectrum.api.setActive()
    hits.api.setActive()
    spectrum.group.api.setSize({ width: Math.round(width / 3), height: Math.round(height * 0.32) })
    match.group.api.setSize({ height: Math.round(height * 0.34) })
  }
  api.getPanel('chromatogram')?.api.setActive()
}

interface SavedLayout {
  version: number
  layout: unknown
}

function loadLayout(api: DockviewApi, kind: DocumentKind): boolean {
  const text = useSession.getState().settings.ui?.layouts?.[kind]
  if (!text) return false
  try {
    const saved = JSON.parse(text) as SavedLayout
    if (saved.version !== LAYOUT_VERSION) return false
    api.fromJSON(saved.layout as Parameters<DockviewApi['fromJSON']>[0])
    return Boolean(api.getPanel('chromatogram'))
  } catch {
    return false
  }
}

// --------------------------------------------------------------- commands
let current: { api: DockviewApi; kind: DocumentKind } | null = null

/** Restore the default layout of the current document kind. */
export function resetLayout() {
  if (!current) return
  buildDefaultLayout(current.api, current.kind)
}

/** Show (or focus) a panel of the current layout. */
export function showPanel(id: PanelId) {
  if (!current) return
  const panel = current.api.getPanel(id)
  if (panel) panel.api.setActive()
  else add(current.api, id, { referencePanel: 'chromatogram', direction: 'right' }).api.setActive()
}

// --------------------------------------------------------------- component
export function Workspace({ kind }: { kind: DocumentKind }) {
  const apiRef = useRef<DockviewApi | null>(null)
  const kindRef = useRef(kind)

  const save = useMemo(
    () =>
      debounce((k: DocumentKind, api: DockviewApi) => {
        const saved: SavedLayout = { version: LAYOUT_VERSION, layout: api.toJSON() }
        void updateSettings({ ui: { layouts: { [k]: JSON.stringify(saved) } } })
      }, 800),
    [],
  )

  const onReady = (event: DockviewReadyEvent) => {
    const api = event.api
    apiRef.current = api
    current = { api, kind }
    if (!loadLayout(api, kind)) buildDefaultLayout(api, kind)
    api.onDidLayoutChange(() => save(kindRef.current, api))
  }

  // switching between an FID and an MS document switches the layout
  useEffect(() => {
    const api = apiRef.current
    if (!api || kindRef.current === kind) return
    save.cancel()
    const saved: SavedLayout = { version: LAYOUT_VERSION, layout: api.toJSON() }
    void updateSettings({ ui: { layouts: { [kindRef.current]: JSON.stringify(saved) } } })
    kindRef.current = kind
    current = { api, kind }
    if (!loadLayout(api, kind)) buildDefaultLayout(api, kind)
  }, [kind, save])

  useEffect(
    () => () => {
      save.cancel()
      current = null
    },
    [save],
  )

  return (
    <DockviewReact
      className="h-full w-full"
      components={components}
      tabComponents={tabComponents}
      onReady={onReady}
      theme={theme}
      disableFloatingGroups={false}
    />
  )
}
