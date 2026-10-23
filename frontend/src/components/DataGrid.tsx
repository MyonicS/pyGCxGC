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
  themeQuartz,
  ValidationModule,
  type GridOptions,
} from 'ag-grid-community'
import { AgGridReact, type AgGridReactProps } from 'ag-grid-react'

// only what the tables use (AllCommunityModule doubles the bundle); the
// validation module reports a missing module during development
ModuleRegistry.registerModules([
  ClientSideRowModelModule,
  PinnedRowModule,
  CellStyleModule,
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
  return <AgGridReact<T> {...defaults} {...props} />
}
