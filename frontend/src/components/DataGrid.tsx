/**
 * AG Grid Community (spec §2: tables), themed with the app's design tokens so
 * it follows light and dark mode (THEME-2). Fonts are the bundled ones; the
 * grid never loads fonts from the network.
 */

import {
  CellStyleModule,
  ClientSideRowModelModule,
  ModuleRegistry,
  PinnedRowModule,
  RowStyleModule,
  TextEditorModule,
  themeQuartz,
  ValidationModule,
  type ColDef,
  type ColGroupDef,
  type GridOptions,
} from 'ag-grid-community'
import { AgGridReact, type AgGridReactProps, type CustomInnerHeaderProps } from 'ag-grid-react'
import { useMemo } from 'react'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

// only what the tables use (AllCommunityModule doubles the bundle); the
// validation module reports a missing module during development
ModuleRegistry.registerModules([
  ClientSideRowModelModule,
  PinnedRowModule,
  CellStyleModule,
  RowStyleModule,
  TextEditorModule,
  ...(import.meta.env.DEV ? [ValidationModule] : []),
])

export const gridTheme = themeQuartz.withParams({
  backgroundColor: 'var(--panel)',
  foregroundColor: 'var(--foreground)',
  textColor: 'var(--foreground)',
  accentColor: 'var(--primary)',
  borderColor: 'var(--border)',
  chromeBackgroundColor: 'var(--chrome)',
  headerBackgroundColor: 'var(--chrome)',
  headerTextColor: 'var(--muted-foreground)',
  rowHoverColor: 'var(--accent)',
  selectedRowBackgroundColor: 'var(--accent)',
  fontFamily: 'inherit',
  fontSize: 12,
  headerFontSize: 11,
  headerFontWeight: 500,
  spacing: 5,
  wrapperBorder: false,
  wrapperBorderRadius: 0,
  browserColorScheme: 'inherit',
})

export function DataGrid<T>(props: AgGridReactProps<T>) {
  const defaults: GridOptions<T> = {
    theme: gridTheme,
    loadThemeGoogleFonts: false,
    suppressCellFocus: true,
    animateRows: false,
  }
  const columnDefs = useMemo(() => props.columnDefs?.map(withHeaderTooltip), [props.columnDefs])
  return <AgGridReact<T> {...defaults} {...props} columnDefs={columnDefs} />
}

/** A header label with the app's tooltip (AG Grid's own needs its TooltipModule). */
function HeaderLabel({ displayName, tooltip }: CustomInnerHeaderProps & { tooltip: string }) {
  return (
    <Tooltip delayDuration={300} disableHoverableContent>
      <TooltipTrigger asChild>
        <span className="truncate">{displayName}</span>
      </TooltipTrigger>
      <TooltipContent side="bottom">{tooltip}</TooltipContent>
    </Tooltip>
  )
}

/** Columns with a `headerTooltip` get {@link HeaderLabel}; sorting and resizing stay AG Grid's. */
function withHeaderTooltip<T>(def: ColDef<T> | ColGroupDef<T>): ColDef<T> | ColGroupDef<T> {
  if ('children' in def || !def.headerTooltip) return def
  const { headerTooltip, ...rest } = def
  return {
    ...rest,
    headerComponentParams: {
      ...def.headerComponentParams,
      innerHeaderComponent: HeaderLabel,
      innerHeaderComponentParams: { tooltip: headerTooltip },
    },
  }
}
