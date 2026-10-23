import { describe, expect, it } from 'vitest'

import data from '@/colormaps.json'

import { colorAt, colormapLut, COLORMAPS, lutIndex } from './colormaps'

describe('colour maps (spec VIEW-4)', () => {
  it('has the eight maps with 256 entries', () => {
    expect(Object.keys(data.maps).sort()).toEqual([...COLORMAPS].sort())
    for (const name of COLORMAPS) expect(colormapLut(name).length).toBe(256 * 4)
  })

  it('indexes like matplotlib Colormap.__call__', () => {
    expect(lutIndex(0)).toBe(0)
    expect(lutIndex(1)).toBe(255)
    expect(lutIndex(0.5)).toBe(128)
    expect(lutIndex(0.5 - 1e-9)).toBe(127)
    expect(lutIndex(-0.1)).toBe(0)
  })

  it('matches matplotlib colours', () => {
    // viridis(x, bytes=True) for x = 0, 0.5, 1 and just below 0.5
    expect(colorAt('viridis', 0)).toEqual([68, 1, 84])
    expect(colorAt('viridis', 0.5)).toEqual([32, 144, 140])
    expect(colorAt('viridis', 1)).toEqual([253, 231, 36])
    expect(colorAt('viridis', 0.5 - 1e-9)).toEqual([32, 143, 140])
    expect(colorAt('gray', 0)).toEqual([0, 0, 0])
    expect(colorAt('gray', 1)).toEqual([255, 255, 255])
  })

  it('reverses', () => {
    expect(colorAt('viridis', 0, true)).toEqual([253, 231, 36])
    expect(colorAt('viridis', 1, true)).toEqual([68, 1, 84])
    expect(colormapLut('magma', true)[3]).toBe(255)
  })
})
