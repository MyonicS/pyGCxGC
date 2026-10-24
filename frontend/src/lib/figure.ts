/**
 * Figure export (spec VIEW-12): plots as standalone SVG files or PNG images
 * at 2× pixel density.
 *
 * The plots are SVG styled by CSS classes and theme tokens. A figure is a
 * clone with the computed styles written into each element, so the file
 * looks like the screen without the app's style sheets. PNGs are rendered
 * from that SVG with the UI fonts embedded (an SVG drawn as an image cannot
 * load fonts). Figures can be made in the light or the dark theme, whatever
 * the app shows (colour maps and mask colours do not change with it).
 */

export type FigureFormat = 'png' | 'svg'
export type FigureTheme = 'light' | 'dark'

/** Presentation properties copied from the computed style. */
const PROPERTIES = [
  'fill',
  'fill-opacity',
  'stroke',
  'stroke-width',
  'stroke-opacity',
  'stroke-dasharray',
  'stroke-linecap',
  'stroke-linejoin',
  'opacity',
  'font-family',
  'font-size',
  'font-weight',
  'font-style',
  'font-variant-numeric',
  'text-anchor',
  'dominant-baseline',
  'paint-order',
  'image-rendering',
] as const

/** Elements marked like this (crosshair, drafts, handles) are left out. */
export const SKIP_ATTRIBUTE = 'data-export-skip'

/** Font families in the file: the UI font, then common ones for other computers. */
const FALLBACK_FONTS = 'Inter, "Helvetica Neue", Helvetica, Arial, sans-serif'

export interface FigureOptions {
  width: number
  height: number
  /** Background colour of the whole figure (default: transparent). */
  background?: string | null
  /** Changes the clone before styles are inlined (e.g. adds the image). */
  prepare?: (clone: SVGSVGElement) => void
}

/**
 * A standalone copy of `svg`: computed styles inlined, classes removed,
 * skipped and hidden elements dropped.
 */
export function standaloneSvg(svg: SVGSVGElement, options: FigureOptions): SVGSVGElement {
  const clone = svg.cloneNode(true) as SVGSVGElement
  inlineStyles(svg, clone)
  for (const el of clone.querySelectorAll(`[${SKIP_ATTRIBUTE}]`)) el.remove()
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('width', String(options.width))
  clone.setAttribute('height', String(options.height))
  clone.setAttribute('viewBox', `0 0 ${options.width} ${options.height}`)
  clone.removeAttribute('class')
  clone.removeAttribute('style')
  options.prepare?.(clone)
  if (options.background) {
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
    rect.setAttribute('width', '100%')
    rect.setAttribute('height', '100%')
    rect.setAttribute('fill', options.background)
    clone.insertBefore(rect, clone.firstChild)
  }
  return clone
}

function inlineStyles(source: Element, target: Element) {
  const style = getComputedStyle(source)
  if (style.display === 'none' || style.visibility === 'hidden') {
    target.setAttribute(SKIP_ATTRIBUTE, '')
    return
  }
  const declarations: string[] = []
  for (const name of PROPERTIES) {
    let value = style.getPropertyValue(name)
    if (!value || value === 'normal' || value === 'auto') continue
    if (name === 'font-family') value = `${value}, ${FALLBACK_FONTS}`
    declarations.push(`${name}:${value}`)
  }
  if (declarations.length) target.setAttribute('style', declarations.join(';'))
  target.removeAttribute('class')
  const from = source.children
  const to = target.children
  for (let i = 0; i < from.length; i++) inlineStyles(from[i], to[i])
}

/** The SVG file. With `embedFonts`, the UI fonts are included as data URLs. */
export async function svgText(svg: SVGSVGElement, embedFonts = false): Promise<string> {
  if (embedFonts) {
    const css = await fontFaceCss()
    if (css) {
      const style = document.createElementNS('http://www.w3.org/2000/svg', 'style')
      style.textContent = css
      svg.insertBefore(style, svg.firstChild)
    }
  }
  return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(svg)}`
}

/** Render an SVG file to a PNG at `scale` × its size. */
export async function svgToPng(
  text: string,
  width: number,
  height: number,
  scale = 2,
): Promise<Blob> {
  const url = URL.createObjectURL(new Blob([text], { type: 'image/svg+xml' }))
  try {
    const img = new Image()
    img.decoding = 'sync'
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error('The figure could not be rendered'))
      img.src = url
    })
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(width * scale)
    canvas.height = Math.round(height * scale)
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('No 2D canvas')
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (b) => (b ? resolve(b) : reject(new Error('PNG encoding failed'))),
        'image/png',
      ),
    )
  } finally {
    URL.revokeObjectURL(url)
  }
}

/**
 * Run `fn` with the page styled in `theme` (synchronously, so nothing is
 * painted in between): computed styles read inside are those of the theme.
 */
export function withTheme<T>(theme: FigureTheme, fn: () => T): T {
  const root = document.documentElement
  const wasDark = root.classList.contains('dark')
  if (wasDark === (theme === 'dark')) return fn()
  // colour transitions would report the old colours
  const freeze = document.createElement('style')
  freeze.textContent = '*,*::before,*::after{transition:none!important}'
  document.head.appendChild(freeze)
  root.classList.toggle('dark', theme === 'dark')
  try {
    return fn()
  } finally {
    root.classList.toggle('dark', wasDark)
    freeze.remove()
  }
}

/** The theme the app shows. */
export function pageTheme(): FigureTheme {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

/**
 * Save an SVG plot (cloned with {@link standaloneSvg} in `theme`) as
 * `name.png` or `name.svg`. `options` is evaluated in that theme too, so it
 * can read computed colours.
 */
export async function saveFigure(
  svg: SVGSVGElement,
  name: string,
  format: FigureFormat,
  theme: FigureTheme,
  options: () => FigureOptions,
): Promise<void> {
  const { figure, width, height } = withTheme(theme, () => {
    const o = options()
    return { figure: standaloneSvg(svg, o), width: o.width, height: o.height }
  })
  if (format === 'svg') {
    const text = await svgText(figure)
    saveBlob(new Blob([text], { type: 'image/svg+xml' }), `${name}.svg`)
  } else {
    const text = await svgText(figure, true)
    saveBlob(await svgToPng(text, width, height), `${name}.png`)
  }
}

/**
 * Save the SVG plot inside `container` (e.g. a stick plot) as it is shown.
 * Throws when nothing is plotted.
 */
export async function saveSvgPlot(
  container: Element | null,
  name: string,
  format: FigureFormat,
  theme: FigureTheme = pageTheme(),
): Promise<void> {
  const svg = container?.querySelector<SVGSVGElement>(':scope > svg')
  if (!svg) throw new Error('There is no plot to save')
  const width = Number(svg.getAttribute('width'))
  const height = Number(svg.getAttribute('height'))
  await saveFigure(svg, name, format, theme, () => ({
    width,
    height,
    background: backgroundOf(container),
  }))
}

/** Download a file (the desktop app will offer a native save dialog). */
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** The first opaque background colour of `el` or its ancestors. */
export function backgroundOf(el: Element | null): string | null {
  for (let e = el; e; e = e.parentElement) {
    const bg = getComputedStyle(e).backgroundColor
    if (bg && bg !== 'transparent' && !/rgba\(.*,\s*0\)$/.test(bg)) return bg
  }
  return null
}

/** A file name from a document name and a suffix (no extension). */
export function figureName(...parts: (string | null | undefined)[]): string {
  return parts
    .filter(Boolean)
    .map((p) => p!.replace(/\.[^.]+$/, '').replace(/[^\w.-]+/g, '-'))
    .join('_')
}

// ------------------------------------------------------------------ fonts
let fontCss: Promise<string> | null = null

/**
 * `@font-face` rules of the UI fonts (Latin subset) with the font files
 * inlined, read from the app's own style sheets.
 */
function fontFaceCss(): Promise<string> {
  fontCss ??= (async () => {
    const rules: CSSFontFaceRule[] = []
    for (const sheet of Array.from(document.styleSheets)) {
      let list: CSSRuleList
      try {
        list = sheet.cssRules
      } catch {
        continue // another origin
      }
      for (const rule of Array.from(list)) {
        if (!(rule instanceof CSSFontFaceRule)) continue
        const range = rule.style.getPropertyValue('unicode-range')
        // the basic Latin subset (U+0000-00FF ...) covers numbers and labels
        if (range && !/U\+0+-0*FF\b/i.test(range)) continue
        rules.push(rule)
      }
    }
    const parts = await Promise.all(
      rules.map(async (rule) => {
        const src = rule.style.getPropertyValue('src')
        const match = /url\(["']?([^"')]+)["']?\)/.exec(src)
        if (!match) return ''
        try {
          const response = await fetch(new URL(match[1], document.baseURI))
          const data = await blobToDataUrl(await response.blob())
          const keep = ['font-family', 'font-style', 'font-weight', 'font-stretch']
            .map((p) => [p, rule.style.getPropertyValue(p)])
            .filter(([, v]) => v)
            .map(([p, v]) => `${p}:${v};`)
            .join('')
          return `@font-face{${keep}src:url(${data});}`
        } catch {
          return ''
        }
      }),
    )
    return parts.join('\n')
  })()
  return fontCss
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.readAsDataURL(blob)
  })
}
