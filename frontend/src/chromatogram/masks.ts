/**
 * Mask overlays (spec VIEW-11). The pixel masks come from Python (spec
 * MASK-2); here they are only combined into one RGBA texture for the shader
 * and traced into outlines along pixel edges for the SVG overlay.
 */

export interface MaskRaster {
  id: string
  color: string
  /** Weights 0–255, row-major `rows × cols`, row 0 = lowest RT2. */
  data: Uint8Array
}

/** Parse `#rgb` / `#rrggbb` into `[r, g, b]` (grey for anything else). */
export function parseHex(color: string): [number, number, number] {
  let hex = color.trim().replace(/^#/, '')
  if (hex.length === 3) hex = [...hex].map((c) => c + c).join('')
  const n = Number.parseInt(hex, 16)
  if (hex.length !== 6 || Number.isNaN(n)) return [128, 128, 128]
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/**
 * Composite masks in order ("over" with the weight as alpha) into a
 * straight-alpha RGBA image of the same grid.
 */
export function composeMasks(masks: MaskRaster[], rows: number, cols: number): Uint8Array {
  const n = rows * cols
  const r = new Float32Array(n)
  const g = new Float32Array(n)
  const b = new Float32Array(n)
  const a = new Float32Array(n)
  for (const mask of masks) {
    const [mr, mg, mb] = parseHex(mask.color)
    const w = mask.data
    for (let i = 0; i < n; i++) {
      const alpha = w[i] / 255
      if (alpha === 0) continue
      const keep = 1 - alpha
      r[i] = mr * alpha + r[i] * keep
      g[i] = mg * alpha + g[i] * keep
      b[i] = mb * alpha + b[i] * keep
      a[i] = alpha + a[i] * keep
    }
  }
  const out = new Uint8Array(n * 4)
  for (let i = 0; i < n; i++) {
    const alpha = a[i]
    if (alpha === 0) continue
    out[i * 4] = Math.round(r[i] / alpha)
    out[i * 4 + 1] = Math.round(g[i] / alpha)
    out[i * 4 + 2] = Math.round(b[i] / alpha)
    out[i * 4 + 3] = Math.round(alpha * 255)
  }
  return out
}

/**
 * SVG path of the boundary of a mask (pixels with a non-zero weight) along
 * pixel edges, in pixel units: x = column, y = row (row 0 at y = 0).
 * Collinear edges are merged into runs.
 */
export function outlinePath(data: Uint8Array, rows: number, cols: number): string {
  const inside = (r: number, c: number) =>
    r >= 0 && r < rows && c >= 0 && c < cols && data[r * cols + c] > 0
  const parts: string[] = []
  // horizontal edges between row y-1 and row y
  for (let y = 0; y <= rows; y++) {
    let start = -1
    for (let c = 0; c <= cols; c++) {
      const edge = c < cols && inside(y - 1, c) !== inside(y, c)
      if (edge && start < 0) start = c
      else if (!edge && start >= 0) {
        parts.push(`M${start} ${y}H${c}`)
        start = -1
      }
    }
  }
  // vertical edges between column x-1 and column x
  for (let x = 0; x <= cols; x++) {
    let start = -1
    for (let r = 0; r <= rows; r++) {
      const edge = r < rows && inside(r, x - 1) !== inside(r, x)
      if (edge && start < 0) start = r
      else if (!edge && start >= 0) {
        parts.push(`M${x} ${start}V${r}`)
        start = -1
      }
    }
  }
  return parts.join('')
}
