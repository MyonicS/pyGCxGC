/**
 * WebGL2 renderer of the 2D chromatogram (spec VIEW-1).
 *
 * The image is uploaded once as an R32F texture. Scaling, contrast, colour
 * mapping and interpolation happen in the fragment shader, so changing them
 * never needs the backend. Float textures cannot be filtered everywhere
 * (`OES_texture_float_linear`), so the shader reads texels with `texelFetch`
 * and interpolates itself, before colour mapping (spec VIEW-7):
 *
 * * `nearest`: the texel containing the point;
 * * `bilinear`: the tent filter on the 2 × 2 nearest texel centres;
 * * `bicubic`: the cubic B-spline on 4 × 4 texels, which is the kernel
 *   matplotlib's `"bicubic"` uses (Agg's `image_filter_bicubic`).
 *
 * Colours follow matplotlib: index `min(floor(s · 256), 255)` into a 256-entry
 * table.
 */

import type { Domain, ImageGeometry } from '@/lib/geometry'
import { SCALING_INDEX, type Scaling } from '@/lib/scaling'
import type { Interpolation } from '@/store/view'

const VERTEX = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() {
  v_uv = a_pos * 0.5 + 0.5;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`

const FRAGMENT = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

uniform sampler2D u_data;     // R32F, width = columns (RT1), height = rows (RT2)
uniform sampler2D u_cmap;     // RGBA8, 256 x 1
uniform sampler2D u_overlay;  // RGBA8 mask overlay
uniform vec4 u_uv;            // texel coordinates of the plot edges: u0, u1, v0, v1
uniform ivec2 u_size;         // columns, rows
uniform vec4 u_ouv;           // the same for the overlay
uniform ivec2 u_osize;
uniform float u_oopacity;     // 0: no overlay
uniform vec2 u_range;         // vmin, vmax
uniform float u_k;            // log dynamic range
uniform int u_scaling;        // 0 linear, 1 sqrt, 2 cbrt, 3 log
uniform int u_interp;         // 0 nearest, 1 bilinear, 2 bicubic
uniform int u_shift;          // phase-shift preview: cyclic roll of the rows, in [0, rows)

in vec2 v_uv;
out vec4 o_color;

float texel(int c, int r) {
  c = clamp(c, 0, u_size.x - 1);
  r = clamp(r, 0, u_size.y - 1);
  if (u_shift != 0) r = (r + u_shift) % u_size.y;
  return texelFetch(u_data, ivec2(c, r), 0).r;
}

vec4 bspline(float f) {
  float g = 1.0 - f;
  float f2 = f * f;
  float f3 = f2 * f;
  return vec4(
    g * g * g,
    3.0 * f3 - 6.0 * f2 + 4.0,
    -3.0 * f3 + 3.0 * f2 + 3.0 * f + 1.0,
    f3
  ) / 6.0;
}

float sampleValue(float u, float v) {
  if (u_interp == 0) return texel(int(floor(u)), int(floor(v)));
  float x = u - 0.5;
  float y = v - 0.5;
  int c = int(floor(x));
  int r = int(floor(y));
  float fx = x - float(c);
  float fy = y - float(r);
  if (u_interp == 1) {
    float a = mix(texel(c, r), texel(c + 1, r), fx);
    float b = mix(texel(c, r + 1), texel(c + 1, r + 1), fx);
    return mix(a, b, fy);
  }
  vec4 wx = bspline(fx);
  vec4 wy = bspline(fy);
  float sum = 0.0;
  for (int j = 0; j < 4; j++) {
    vec4 row = vec4(
      texel(c - 1, r - 1 + j),
      texel(c, r - 1 + j),
      texel(c + 1, r - 1 + j),
      texel(c + 2, r - 1 + j)
    );
    sum += wy[j] * dot(wx, row);
  }
  return sum;
}

vec4 colorOf(float value) {
  float range = u_range.y - u_range.x;
  float t = range > 0.0
    ? clamp((value - u_range.x) / range, 0.0, 1.0)
    : (value > u_range.x ? 1.0 : 0.0);
  float s = t;
  if (u_scaling == 1) s = sqrt(t);
  else if (u_scaling == 2) s = t > 0.0 ? pow(t, 1.0 / 3.0) : 0.0;
  else if (u_scaling == 3 && u_k > 0.0) s = log(1.0 + u_k * t) / log(1.0 + u_k);
  int index = clamp(int(floor(s * 256.0)), 0, 255);
  return texelFetch(u_cmap, ivec2(index, 0), 0);
}

void main() {
  float u = mix(u_uv.x, u_uv.y, v_uv.x);
  float v = mix(u_uv.z, u_uv.w, v_uv.y);
  if (u < 0.0 || v < 0.0 || u >= float(u_size.x) || v >= float(u_size.y)) discard;
  float value = sampleValue(u, v);
  if (isnan(value)) discard;
  vec4 color = colorOf(value);
  if (u_oopacity > 0.0) {
    float ou = mix(u_ouv.x, u_ouv.y, v_uv.x);
    float ov = mix(u_ouv.z, u_ouv.w, v_uv.y);
    if (ou >= 0.0 && ov >= 0.0 && ou < float(u_osize.x) && ov < float(u_osize.y)) {
      vec4 o = texelFetch(u_overlay, ivec2(int(floor(ou)), int(floor(ov))), 0);
      color.rgb = mix(color.rgb, o.rgb, o.a * u_oopacity);
    }
  }
  o_color = color;
}`

/** A float image to draw (row-major, row 0 = lowest RT2). */
export interface ImageLayer {
  data: Float32Array
  rows: number
  cols: number
  geometry: ImageGeometry
}

/** An RGBA8 overlay on its own grid (row-major, row 0 = lowest RT2). */
export interface OverlayLayer {
  rgba: Uint8Array
  rows: number
  cols: number
  geometry: ImageGeometry
}

export interface DrawParams {
  domain: Domain
  vmin: number
  vmax: number
  scaling: Scaling
  k: number
  interpolation: Interpolation
  /** RGBA lookup table (256 × 4). */
  lut: Uint8Array
  /** Phase-shift preview in overview rows (positive moves peaks down). */
  shiftRows: number
  overlayOpacity: number
}

const INTERP_INDEX: Record<Interpolation, number> = { nearest: 0, bilinear: 1, bicubic: 2 }

let cachedMaxTexture: number | null = null

/** `MAX_TEXTURE_SIZE` of this device (4096 if WebGL2 is unavailable). */
export function maxTextureSize(): number {
  if (cachedMaxTexture !== null) return cachedMaxTexture
  let size = 4096
  try {
    const canvas = document.createElement('canvas')
    const gl = canvas.getContext('webgl2')
    if (gl) {
      size = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number
      gl.getExtension('WEBGL_lose_context')?.loseContext()
    }
  } catch {
    // keep the default
  }
  cachedMaxTexture = size
  return size
}

export class WebGLUnavailableError extends Error {
  constructor() {
    super('WebGL2 is not available in this browser')
  }
}

type Uniforms = Record<
  | 'u_data'
  | 'u_cmap'
  | 'u_overlay'
  | 'u_uv'
  | 'u_size'
  | 'u_ouv'
  | 'u_osize'
  | 'u_oopacity'
  | 'u_range'
  | 'u_k'
  | 'u_scaling'
  | 'u_interp'
  | 'u_shift',
  WebGLUniformLocation | null
>

export class ChromRenderer {
  private readonly canvas: HTMLCanvasElement
  private gl: WebGL2RenderingContext
  private program!: WebGLProgram
  private uniforms!: Uniforms
  private vao!: WebGLVertexArrayObject
  private textures: Record<'overview' | 'detail' | 'cmap' | 'overlay', WebGLTexture | null> = {
    overview: null,
    detail: null,
    cmap: null,
    overlay: null,
  }
  private layers: { overview: ImageLayer | null; detail: ImageLayer | null } = {
    overview: null,
    detail: null,
  }
  private overlay: OverlayLayer | null = null
  private lut: Uint8Array | null = null
  private lost = false
  private readonly onLost = (e: Event) => {
    e.preventDefault()
    this.lost = true
  }
  private readonly onRestored = () => {
    this.lost = false
    this.init()
    const { overview, detail } = this.layers
    this.layers = { overview: null, detail: null }
    const overlay = this.overlay
    this.overlay = null
    this.lut = null
    this.setLayer('overview', overview)
    this.setLayer('detail', detail)
    this.setOverlay(overlay)
    this.onRestore?.()
  }
  /** Called after the context was lost and restored (to redraw). */
  onRestore?: () => void

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    const gl = canvas.getContext('webgl2', {
      alpha: true,
      antialias: false,
      premultipliedAlpha: false,
      preserveDrawingBuffer: true, // figure export reads the canvas
    })
    if (!gl) throw new WebGLUnavailableError()
    this.gl = gl
    canvas.addEventListener('webglcontextlost', this.onLost)
    canvas.addEventListener('webglcontextrestored', this.onRestored)
    this.init()
  }

  private init() {
    const gl = this.gl
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type)!
      gl.shaderSource(shader, source)
      gl.compileShader(shader)
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        throw new Error(`Shader: ${gl.getShaderInfoLog(shader)}`)
      }
      return shader
    }
    const program = gl.createProgram()!
    gl.attachShader(program, compile(gl.VERTEX_SHADER, VERTEX))
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAGMENT))
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(`Program: ${gl.getProgramInfoLog(program)}`)
    }
    this.program = program
    const names = [
      'u_data',
      'u_cmap',
      'u_overlay',
      'u_uv',
      'u_size',
      'u_ouv',
      'u_osize',
      'u_oopacity',
      'u_range',
      'u_k',
      'u_scaling',
      'u_interp',
      'u_shift',
    ] as const
    this.uniforms = Object.fromEntries(
      names.map((n) => [n, gl.getUniformLocation(program, n)]),
    ) as Uniforms

    this.vao = gl.createVertexArray()!
    gl.bindVertexArray(this.vao)
    const buffer = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
    const loc = gl.getAttribLocation(program, 'a_pos')
    gl.enableVertexAttribArray(loc)
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
    gl.bindVertexArray(null)
    this.textures = { overview: null, detail: null, cmap: null, overlay: null }
  }

  private texture(slot: keyof ChromRenderer['textures']): WebGLTexture {
    const gl = this.gl
    let tex = this.textures[slot]
    if (!tex) {
      tex = gl.createTexture()!
      gl.bindTexture(gl.TEXTURE_2D, tex)
      // texelFetch needs a complete texture: no mipmaps, nearest filtering
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
      this.textures[slot] = tex
    }
    return tex
  }

  /** Upload an image layer (skipped when it is the same object as before). */
  setLayer(slot: 'overview' | 'detail', layer: ImageLayer | null) {
    if (this.layers[slot] === layer) return
    this.layers[slot] = layer
    if (!layer || this.lost) return
    const gl = this.gl
    gl.bindTexture(gl.TEXTURE_2D, this.texture(slot))
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.R32F,
      layer.cols,
      layer.rows,
      0,
      gl.RED,
      gl.FLOAT,
      layer.data,
    )
  }

  setOverlay(overlay: OverlayLayer | null) {
    if (this.overlay === overlay) return
    this.overlay = overlay
    if (!overlay || this.lost) return
    const gl = this.gl
    gl.bindTexture(gl.TEXTURE_2D, this.texture('overlay'))
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.RGBA8,
      overlay.cols,
      overlay.rows,
      0,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      overlay.rgba,
    )
  }

  private setLut(lut: Uint8Array) {
    if (this.lut === lut) return
    this.lut = lut
    const gl = this.gl
    gl.bindTexture(gl.TEXTURE_2D, this.texture('cmap'))
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, lut)
  }

  /** Match the drawing buffer to the canvas' CSS size times the pixel ratio. */
  resize(cssWidth: number, cssHeight: number, dpr = window.devicePixelRatio || 1) {
    const w = Math.max(1, Math.round(cssWidth * dpr))
    const h = Math.max(1, Math.round(cssHeight * dpr))
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w
      this.canvas.height = h
    }
  }

  draw(p: DrawParams) {
    if (this.lost) return
    const gl = this.gl
    const u = this.uniforms
    gl.viewport(0, 0, this.canvas.width, this.canvas.height)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
    const { overview, detail } = this.layers
    if (!overview) return
    gl.useProgram(this.program)
    gl.bindVertexArray(this.vao)
    this.setLut(p.lut)

    gl.activeTexture(gl.TEXTURE1)
    gl.bindTexture(gl.TEXTURE_2D, this.textures.cmap)
    gl.uniform1i(u.u_cmap, 1)
    const overlay = this.overlay
    gl.activeTexture(gl.TEXTURE2)
    gl.bindTexture(gl.TEXTURE_2D, overlay ? this.texture('overlay') : null)
    gl.uniform1i(u.u_overlay, 2)
    if (overlay && p.overlayOpacity > 0) {
      gl.uniform4fv(u.u_ouv, texelEdges(overlay.geometry, p.domain))
      gl.uniform2i(u.u_osize, overlay.cols, overlay.rows)
      gl.uniform1f(u.u_oopacity, p.overlayOpacity)
    } else {
      gl.uniform1f(u.u_oopacity, 0)
    }
    gl.uniform2f(u.u_range, p.vmin, p.vmax)
    gl.uniform1f(u.u_k, p.k)
    gl.uniform1i(u.u_scaling, SCALING_INDEX[p.scaling])
    gl.uniform1i(u.u_interp, INTERP_INDEX[p.interpolation])
    gl.uniform1i(u.u_data, 0)

    const drawLayer = (layer: ImageLayer, slot: 'overview' | 'detail', shift: number) => {
      gl.activeTexture(gl.TEXTURE0)
      gl.bindTexture(gl.TEXTURE_2D, this.textures[slot])
      gl.uniform4fv(u.u_uv, texelEdges(layer.geometry, p.domain))
      gl.uniform2i(u.u_size, layer.cols, layer.rows)
      gl.uniform1i(u.u_shift, ((shift % layer.rows) + layer.rows) % layer.rows)
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
    }
    drawLayer(overview, 'overview', p.shiftRows)
    // the detail layer cannot preview a cyclic shift (it lacks the wrapped rows)
    if (detail && p.shiftRows === 0) drawLayer(detail, 'detail', 0)
    gl.bindVertexArray(null)
  }

  /** RGBA pixels of the last frame, bottom row first (`width × height × 4`). */
  readPixels(): Uint8Array {
    const gl = this.gl
    const out = new Uint8Array(this.canvas.width * this.canvas.height * 4)
    gl.readPixels(0, 0, this.canvas.width, this.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, out)
    return out
  }

  dispose() {
    this.canvas.removeEventListener('webglcontextlost', this.onLost)
    this.canvas.removeEventListener('webglcontextrestored', this.onRestored)
    const gl = this.gl
    for (const tex of Object.values(this.textures)) if (tex) gl.deleteTexture(tex)
    gl.deleteProgram(this.program)
    gl.deleteVertexArray(this.vao)
  }
}

/**
 * Texel coordinates `[u0, u1, v0, v1]` of the plot edges, computed in double
 * precision so deep zooms stay exact.
 */
export function texelEdges(g: ImageGeometry, d: Domain): Float32Array {
  return new Float32Array([
    (d.x0 - g.rt1.start) / g.rt1.step,
    (d.x1 - g.rt1.start) / g.rt1.step,
    (d.y0 - g.rt2.start) / g.rt2.step,
    (d.y1 - g.rt2.start) / g.rt2.step,
  ])
}
