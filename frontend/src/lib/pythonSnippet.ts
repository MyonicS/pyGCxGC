/**
 * "Copy as Python" (spec INT-5): code that reproduces the processing and the
 * integration of a document with the public API, using the exported mask set.
 */

import type { DocumentInfo } from '@/api/types'

/** A JSON value as a Python literal. */
export function pythonLiteral(value: unknown): string {
  if (value === null || value === undefined) return 'None'
  if (value === true) return 'True'
  if (value === false) return 'False'
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : 'float("nan")'
  if (typeof value === 'string') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(pythonLiteral).join(', ')}]`
  const entries = Object.entries(value as Record<string, unknown>)
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}: ${pythonLiteral(v)}`).join(', ')}}`
}

/** File name of the exported mask set (as the backend names it). */
export function maskSetFilename(doc: DocumentInfo): string {
  const stem = doc.name.replace(/\.[^.]+$/, '') || 'chromatogram'
  return `${stem}.gcxgc-masks.json`
}

function pathLiteral(doc: DocumentInfo): string {
  return doc.path ? `r${JSON.stringify(doc.path).replace(/\\\\/g, '\\')}` : '"<path to the file>"'
}

export function pythonSnippet(doc: DocumentInfo): string {
  const p = doc.params as Record<string, unknown>
  const masks = maskSetFilename(doc)
  if (doc.kind === 'fid') {
    const dt = Number(p.sampling_interval)
    const sampling = doc.sampling_inferred
      ? '"infer"'
      : `${Number((dt * 1000).toPrecision(12))},  # ms`
    const none = (v: unknown) => (v === 'none' ? 'None' : pythonLiteral(v))
    return [
      'import pyGCxGC as gcgc',
      '',
      'chrom = gcgc.parse_2D_chromatogram(',
      `    ${pathLiteral(doc)},`,
      `    modulation_time=${pythonLiteral(p.modulation_time)},`,
      `    sampling_interval=${sampling}${doc.sampling_inferred ? ',' : ''}`,
      `    shift=${pythonLiteral(p.shift ?? 0)},`,
      `    solvent_cutoff=${pythonLiteral(p.solvent_cutoff ?? 0)},`,
      `    baseline_type=${none(p.baseline)},`,
      `    normalize=${none(p.normalize)},`,
      ')',
      `masks = gcgc.MaskSet.load(${JSON.stringify(masks)})`,
      'volumes = masks.integrate(chrom)  # {mask name: volume}',
      'table = masks.integration(chrom)  # volume, percent and pixels, plus "unassigned"',
      '',
    ].join('\n')
  }
  const eic = doc.signal === 'eic' && doc.mz?.length
  const signal = eic ? `data.eic_2d(${pythonLiteral(doc.mz)})[::-1]` : null
  return [
    'import pyGCxGC as gcgc',
    '',
    'data = gcgc.read_gcxgc_ms(',
    `    ${pathLiteral(doc)},`,
    `    modulation_time=${pythonLiteral(p.modulation_time)},`,
    `    shift=${pythonLiteral(p.shift ?? 0)},`,
    `    baseline=${pythonLiteral(p.baseline)},`,
    ')',
    `masks = gcgc.MaskSet.load(${JSON.stringify(masks)})`,
    signal
      ? `volumes = masks.integrate(data, data=${signal})  # EIC m/z ${doc.mz?.join(', ')}`
      : 'volumes = masks.integrate(data)  # TIC',
    signal ? `table = masks.integration(data, data=${signal})` : 'table = masks.integration(data)',
    '',
  ].join('\n')
}
