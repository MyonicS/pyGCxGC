/**
 * Export of the chromatogram view as a figure (spec VIEW-12): the visible
 * region with axes, colour bar, mask outlines and peak markers. The view of
 * each document registers how to export itself.
 */

import { pageTheme, type FigureFormat, type FigureTheme } from '@/lib/figure'
import { notifyError } from '@/lib/notify'

type Exporter = (format: FigureFormat, theme: FigureTheme) => Promise<void>

const exporters = new Map<string, Exporter>()

/** Called by the view of a document; returns a function that unregisters. */
export function registerChromFigure(docId: string, exporter: Exporter): () => void {
  exporters.set(docId, exporter)
  return () => {
    if (exporters.get(docId) === exporter) exporters.delete(docId)
  }
}

/** Save the chromatogram of a document as it is shown (PNG at 2× or SVG). */
export async function exportChromatogram(
  docId: string,
  format: FigureFormat,
  theme: FigureTheme = pageTheme(),
): Promise<void> {
  const exporter = exporters.get(docId)
  try {
    if (!exporter) throw new Error('The chromatogram is not shown')
    await exporter(format, theme)
  } catch (err) {
    notifyError('Export figure', err)
  }
}
