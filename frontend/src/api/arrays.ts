/**
 * Decoder of the binary array format of the API (`pyGCxGC.app.arrays`).
 *
 * A body is a little-endian uint32 header length `n`, `n` bytes of UTF-8 JSON
 * (padded with spaces), then the raw little-endian array data, starting at a
 * multiple of 8 bytes so that it can be viewed without copying.
 */

export const ARRAY_MEDIA_TYPE = 'application/vnd.pygcxgc.array'

const ALIGN = 8

const CONSTRUCTORS = {
  float32: Float32Array,
  float64: Float64Array,
  uint8: Uint8Array,
  uint16: Uint16Array,
  int32: Int32Array,
  uint32: Uint32Array,
} as const

export type DType = keyof typeof CONSTRUCTORS
export type TypedArray = InstanceType<(typeof CONSTRUCTORS)[DType]>

export interface ArrayHeader {
  shape: number[]
  dtype: DType
  [key: string]: unknown
}

export interface DecodedArray<H extends object = Record<string, unknown>> {
  header: ArrayHeader & H
  data: TypedArray
}

const LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1

export function decodeArray<H extends object = Record<string, unknown>>(
  buffer: ArrayBuffer,
): DecodedArray<H> {
  if (buffer.byteLength < 4) throw new Error('Array body too short')
  const view = new DataView(buffer)
  const n = view.getUint32(0, true)
  if (4 + n > buffer.byteLength) throw new Error('Array header exceeds the body')
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, n))) as ArrayHeader &
    H
  const Ctor = CONSTRUCTORS[header.dtype]
  if (!Ctor) throw new Error(`Unsupported dtype ${String(header.dtype)}`)
  let offset = 4 + n
  offset += (ALIGN - (offset % ALIGN)) % ALIGN
  const count = header.shape.reduce((a, b) => a * b, 1)
  if (offset + count * Ctor.BYTES_PER_ELEMENT > buffer.byteLength) {
    throw new Error('Array data shorter than its shape')
  }
  let data: TypedArray
  if (LITTLE_ENDIAN || Ctor.BYTES_PER_ELEMENT === 1) {
    data = new Ctor(buffer, offset, count)
  } else {
    data = swapToHost(view, offset, count, header.dtype)
  }
  return { header, data }
}

function swapToHost(view: DataView, offset: number, count: number, dtype: DType): TypedArray {
  const out = new CONSTRUCTORS[dtype](count)
  const size = out.BYTES_PER_ELEMENT
  const get = {
    float32: (o: number) => view.getFloat32(o, true),
    float64: (o: number) => view.getFloat64(o, true),
    uint16: (o: number) => view.getUint16(o, true),
    int32: (o: number) => view.getInt32(o, true),
    uint32: (o: number) => view.getUint32(o, true),
    uint8: (o: number) => view.getUint8(o),
  }[dtype]
  for (let i = 0; i < count; i++) out[i] = get(offset + i * size)
  return out
}

/** Encoder with the same layout (used by tests and stubs). */
export function encodeArray(
  data: TypedArray,
  shape: number[],
  dtype: DType,
  header: Record<string, unknown> = {},
): ArrayBuffer {
  let text = new TextEncoder().encode(JSON.stringify({ ...header, shape, dtype }))
  const pad = (ALIGN - ((4 + text.length) % ALIGN)) % ALIGN
  if (pad) {
    const padded = new Uint8Array(text.length + pad).fill(0x20)
    padded.set(text)
    text = padded
  }
  const out = new ArrayBuffer(4 + text.length + data.byteLength)
  new DataView(out).setUint32(0, text.length, true)
  new Uint8Array(out, 4, text.length).set(text)
  new Uint8Array(out, 4 + text.length).set(
    new Uint8Array(data.buffer, data.byteOffset, data.byteLength),
  )
  return out
}
