import { describe, expect, it } from 'vitest'

import { decodeArray, encodeArray } from './arrays'

const fromBase64 = (s: string) => {
  const bytes = Uint8Array.from(atob(s), (c) => c.charCodeAt(0))
  return bytes.buffer
}

describe('decodeArray', () => {
  it('reads bodies written by pyGCxGC.app.arrays.encode_array', () => {
    // encode_array(np.arange(6, dtype=float32).reshape(2, 3) - 1.5, {...})
    const body = fromBase64(
      'XAAAAHsibmFtZSI6IngiLCJydDEiOnsic3RhcnQiOjAuNSwic3RlcCI6MC4yNSwiY291bnQiOjN9LCJzaGFwZSI6WzIsM10sImR0eXBlIjoiZmxvYXQzMiJ9ICAgICAgAADAvwAAAL8AAAA/AADAPwAAIEAAAGBA',
    )
    const { header, data } = decodeArray<{ name: string; rt1: { start: number } }>(body)
    expect(header.shape).toEqual([2, 3])
    expect(header.dtype).toBe('float32')
    expect(header.name).toBe('x')
    expect(header.rt1.start).toBe(0.5)
    expect(data).toBeInstanceOf(Float32Array)
    expect(Array.from(data)).toEqual([-1.5, -0.5, 0.5, 1.5, 2.5, 3.5])
    // aligned: viewed without copying
    expect(data.byteOffset % 8).toBe(0)
    expect(data.buffer).toBe(body)
  })

  it('reads uint8 rasters', () => {
    const body = fromBase64(
      'NAAAAHsibWFza19pZCI6Im0iLCJzaGFwZSI6WzIsMl0sImR0eXBlIjoidWludDgifSAgICAgICAA/weA',
    )
    const { header, data } = decodeArray(body)
    expect(header.mask_id).toBe('m')
    expect(Array.from(data)).toEqual([0, 255, 7, 128])
  })

  it('round-trips every dtype', () => {
    const cases = [
      [new Float32Array([1.5, -2, 3]), 'float32'],
      [new Float64Array([1e-300, 2, Math.PI]), 'float64'],
      [new Uint8Array([0, 1, 255]), 'uint8'],
      [new Uint16Array([0, 65535, 7]), 'uint16'],
      [new Int32Array([-5, 0, 2 ** 30]), 'int32'],
      [new Uint32Array([0, 2 ** 32 - 1, 3]), 'uint32'],
    ] as const
    for (const [array, dtype] of cases) {
      const { header, data } = decodeArray(encodeArray(array, [3], dtype, { k: 'v' }))
      expect(header.dtype).toBe(dtype)
      expect(header.k).toBe('v')
      expect(Array.from(data)).toEqual(Array.from(array))
    }
  })

  it('rejects truncated bodies', () => {
    const body = encodeArray(new Float32Array(4), [4], 'float32')
    expect(() => decodeArray(body.slice(0, body.byteLength - 4))).toThrow(/shorter/)
    expect(() => decodeArray(new ArrayBuffer(2))).toThrow()
  })
})
