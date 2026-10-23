import { describe, expect, it } from 'vitest'

import { composeMasks, outlinePath, parseHex } from './masks'

describe('mask overlays', () => {
  it('parses colours', () => {
    expect(parseHex('#e6194b')).toEqual([230, 25, 75])
    expect(parseHex('#fff')).toEqual([255, 255, 255])
    expect(parseHex('nope')).toEqual([128, 128, 128])
  })

  it('composites masks in order', () => {
    const red = { id: 'a', color: '#ff0000', data: new Uint8Array([255, 255, 0]) }
    const blue = { id: 'b', color: '#0000ff', data: new Uint8Array([0, 255, 0]) }
    const rgba = composeMasks([red, blue], 1, 3)
    expect(Array.from(rgba.slice(0, 4))).toEqual([255, 0, 0, 255])
    expect(Array.from(rgba.slice(4, 8))).toEqual([0, 0, 255, 255]) // blue drawn over red
    expect(Array.from(rgba.slice(8, 12))).toEqual([0, 0, 0, 0])
    // partial weights (imported TIFFs) blend
    const half = composeMasks([{ id: 'c', color: '#ffffff', data: new Uint8Array([128]) }], 1, 1)
    expect(Array.from(half)).toEqual([255, 255, 255, 128])
  })

  it('traces the outline along pixel edges', () => {
    // 3 x 3 grid, inside: the 2 x 2 block at rows 0-1, cols 1-2
    const data = new Uint8Array([0, 1, 1, 0, 1, 1, 0, 0, 0])
    const path = outlinePath(data, 3, 3)
    expect(path).toBe('M1 0H3M1 2H3M1 0V2M3 0V2')
    expect(outlinePath(new Uint8Array(4), 2, 2)).toBe('')
  })
})
