/**
 * Settings: theme, the default display of new documents (spec THEME-1,
 * VIEW-8), and where settings, caches and logs are kept (FILE-1/2).
 */

import { create } from 'zustand'

import type { ThemeSetting } from '@/api/types'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { COLORMAPS, colormapGradient } from '@/lib/colormaps'
import { SCALING_LABELS, SCALINGS } from '@/lib/scaling'
import { openLogFolder, updateSettings, useSession } from '@/store/session'
import { defaultDisplay, INTERPOLATION_LABELS, INTERPOLATIONS } from '@/store/view'

import { NumberField } from '@/chromatogram/DisplayPopover'

import { setTheme, useThemeSetting } from './theme'
import { resetLayout } from './Workspace'

const useSettingsDialog = create<{ open: boolean }>(() => ({ open: false }))

export function openSettings() {
  useSettingsDialog.setState({ open: true })
}

export function SettingsDialog() {
  const open = useSettingsDialog((s) => s.open)
  const settings = useSession((s) => s.settings)
  const theme = useThemeSetting()
  const d = defaultDisplay(settings)
  const modulation = settings.fid?.modulation_time
  const files = useSession((s) => s.files)

  return (
    <Dialog open={open} onOpenChange={(o) => useSettingsDialog.setState({ open: o })}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>Saved in the pyGCxGC configuration folder.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <Field label="Theme">
            <Choice
              value={theme}
              options={[
                ['system', 'System'],
                ['light', 'Light'],
                ['dark', 'Dark'],
              ]}
              onChange={(v) => setTheme(v as ThemeSetting)}
            />
          </Field>
          <h3 className="pt-2 text-xs font-medium text-muted-foreground">
            Display of new documents
          </h3>
          <Field label="Colour map">
            <Choice
              value={d.colormap}
              options={COLORMAPS.map((c) => [c, c])}
              onChange={(colormap) => void updateSettings({ display: { colormap } })}
              swatch
            />
          </Field>
          <Field label="Scaling">
            <Choice
              value={d.scaling}
              options={SCALINGS.map((s) => [s, SCALING_LABELS[s]])}
              onChange={(scaling) => void updateSettings({ display: { scaling } })}
            />
          </Field>
          <Field label="Interpolation">
            <Choice
              value={d.interpolation}
              options={INTERPOLATIONS.map((i) => [i, INTERPOLATION_LABELS[i]])}
              onChange={(interpolation) => void updateSettings({ display: { interpolation } })}
            />
          </Field>
          <h3 className="pt-2 text-xs font-medium text-muted-foreground">FID files</h3>
          <div className="grid grid-cols-[8rem_1fr] items-end gap-2">
            <NumberField
              id="settings-mod"
              label="Modulation time"
              unit="s"
              value={modulation ?? NaN}
              onCommit={(v) => v > 0 && void updateSettings({ fid: { modulation_time: v } })}
            />
            <Button
              variant="ghost"
              size="sm"
              className="justify-self-start"
              disabled={modulation == null}
              onClick={() => void updateSettings({ fid: { modulation_time: null } })}
            >
              Ask each time
            </Button>
          </div>
          {files && (
            <>
              <h3 className="pt-2 text-xs font-medium text-muted-foreground">Files</h3>
              <dl className="grid grid-cols-[8rem_1fr] gap-x-2 gap-y-1 text-xs">
                <dt>Settings</dt>
                <dd className="truncate font-mono" title={files.settings ?? ''}>
                  {files.settings ?? 'in memory'}
                </dd>
                <dt>Cache</dt>
                <dd className="truncate font-mono" title={files.cache}>
                  {files.cache}
                </dd>
                <dt>Logs</dt>
                <dd className="flex min-w-0 items-center gap-2">
                  <span className="truncate font-mono" title={files.logs}>
                    {files.logs}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 shrink-0 px-2 text-xs"
                    onClick={() => void openLogFolder()}
                  >
                    Open
                  </Button>
                </dd>
              </dl>
              <p className="text-[11px] text-muted-foreground">
                Logs stay on this computer; nothing is sent anywhere.
              </p>
            </>
          )}
        </div>
        <DialogFooter className="sm:justify-between">
          <Button variant="outline" size="sm" onClick={() => resetLayout()}>
            Reset layout
          </Button>
          <Button size="sm" onClick={() => useSettingsDialog.setState({ open: false })}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[8rem_1fr] items-center gap-2">
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  )
}

function Choice({
  value,
  options,
  onChange,
  swatch,
}: {
  value: string
  options: (readonly [string, string])[]
  onChange: (v: string) => void
  swatch?: boolean
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger size="sm" className="w-full text-xs">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map(([v, label]) => (
          <SelectItem key={v} value={v} className="text-xs">
            {swatch && (
              <span
                className="h-2.5 w-10 rounded-sm"
                style={{ backgroundImage: colormapGradient(v as (typeof COLORMAPS)[number]) }}
              />
            )}
            {label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
