import { describe, expect, it } from 'vitest'

import { joinPath } from '@/shell/PathDialog'

import { restoreDisplay, restoreDomain } from './viewSync'
import { defaultDisplay } from './view'

const settings = { display: { colormap: 'plasma' } }

describe('restoreDisplay', () => {
  it('keeps valid fields over the defaults', () => {
    const d = restoreDisplay(
      {
        colormap: 'magma',
        reverse: true,
        scaling: 'log',
        k: 50,
        interpolation: 'bicubic',
        contrast: 'manual',
        vmin: 1,
        vmax: 9,
        maskOpacity: 0.6,
        showMasks: false,
        showPeaks: false,
        colorbarLabel: 'FID signal (pA)',
        showColorbarLabel: false,
      },
      settings,
    )
    expect(d).toEqual({
      colormap: 'magma',
      reverse: true,
      scaling: 'log',
      k: 50,
      interpolation: 'bicubic',
      vmin: 1,
      vmax: 9,
      contrast: 'manual',
      maskOpacity: 0.6,
      showMasks: false,
      showPeaks: false,
      colorbarLabel: 'FID signal (pA)',
      showColorbarLabel: false,
    })
  })

  it('drops invalid fields and automatic contrast', () => {
    const d = restoreDisplay(
      { colormap: 'rainbow', scaling: 'cubic', k: -1, contrast: 'auto', vmin: 1, vmax: 2 },
      settings,
    )
    expect(d).toEqual(defaultDisplay(settings))
    expect(d.colormap).toBe('plasma')
    expect(restoreDisplay(null, settings)).toEqual(defaultDisplay(settings))
    expect(restoreDisplay({ contrast: 'manual', vmin: 5, vmax: 1 }, settings).contrast).toBe('auto')
    expect(restoreDisplay({ maskOpacity: 3 }, settings).maskOpacity).toBe(1)
  })
})

describe('restoreDomain', () => {
  it('accepts proper domains only', () => {
    expect(restoreDomain({ x0: 1, x1: 2, y0: 0, y1: 5 })).toEqual({ x0: 1, x1: 2, y0: 0, y1: 5 })
    expect(restoreDomain({ x0: 2, x1: 1, y0: 0, y1: 5 })).toBeUndefined()
    expect(restoreDomain({ x0: 1, x1: 2 })).toBeUndefined()
    expect(restoreDomain(null)).toBeUndefined()
  })
})

describe('joinPath', () => {
  it('uses the folder separator', () => {
    expect(joinPath('/home/a', 'p.gcxgc')).toBe('/home/a/p.gcxgc')
    expect(joinPath('/', 'p.gcxgc')).toBe('/p.gcxgc')
    expect(joinPath('C:\\data', 'p.gcxgc')).toBe('C:\\data\\p.gcxgc')
  })
})
