import { describe, expect, it } from 'vitest'

import type { DocumentInfo } from '@/api/types'

import { maskSetFilename, pythonLiteral, pythonSnippet } from './pythonSnippet'

const fid = {
  kind: 'fid',
  name: 'Example_FID.csv',
  path: '/data/Example_FID.csv',
  params: {
    modulation_time: 20,
    sampling_interval: 0.004,
    shift: 3,
    solvent_cutoff: 0,
    baseline: 'none',
    normalize: 'volume',
  },
  sampling_inferred: true,
} as unknown as DocumentInfo

describe('Copy as Python', () => {
  it('writes Python literals', () => {
    expect(pythonLiteral({ mode: 'rolling_percentile', window: 2, x: null, on: true })).toBe(
      '{"mode": "rolling_percentile", "window": 2, "x": None, "on": True}',
    )
    expect(pythonLiteral([91, 105])).toBe('[91, 105]')
  })

  it('names the mask set like the export', () => {
    expect(maskSetFilename(fid)).toBe('Example_FID.gcxgc-masks.json')
  })

  it('reproduces FID processing', () => {
    const code = pythonSnippet(fid)
    expect(code).toContain('chrom = gcgc.parse_2D_chromatogram(\n    r"/data/Example_FID.csv",')
    expect(code).toContain('sampling_interval="infer"')
    expect(code).toContain('shift=3,')
    expect(code).toContain('baseline_type=None,')
    expect(code).toContain('normalize="volume",')
    expect(code).toContain('masks = gcgc.MaskSet.load("Example_FID.gcxgc-masks.json")')
    const explicit = pythonSnippet({ ...fid, sampling_inferred: false } as DocumentInfo)
    expect(explicit).toContain('sampling_interval=4,  # ms')
  })

  it('integrates the current MS signal', () => {
    const ms = {
      kind: 'ms',
      name: 'run.cdf',
      path: 'C:\\runs\\run.cdf',
      params: { modulation_time: 20, shift: 0, baseline: { mode: 'none' } },
      signal: 'eic',
      mz: [91, 105],
    } as unknown as DocumentInfo
    const code = pythonSnippet(ms)
    expect(code).toContain('r"C:\\runs\\run.cdf"')
    expect(code).toContain('baseline={"mode": "none"},')
    expect(code).toContain('masks.integrate(data, data=data.eic_2d([91, 105])[::-1])')
  })
})
