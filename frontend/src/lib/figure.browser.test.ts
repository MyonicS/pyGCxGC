import { afterEach, describe, expect, it } from 'vitest'

import {
  figureName,
  pageTheme,
  standaloneSvg,
  svgText,
  svgToPng,
  SKIP_ATTRIBUTE,
  withTheme,
} from './figure'

const NS = 'http://www.w3.org/2000/svg'

function plot(): SVGSVGElement {
  const style = document.createElement('style')
  style.id = 'figure-test-style'
  style.textContent =
    '.axis { stroke: rgb(255, 0, 0); stroke-width: 2px } .label { fill: rgb(0, 0, 255); font-size: 13px }'
  document.head.appendChild(style)
  const svg = document.createElementNS(NS, 'svg')
  svg.setAttribute('width', '120')
  svg.setAttribute('height', '80')
  svg.setAttribute('class', 'block')
  svg.innerHTML = `
    <line class="axis" x1="0" y1="70" x2="120" y2="70"></line>
    <text class="label" x="10" y="20">RT1 (min)</text>
    <line class="axis" ${SKIP_ATTRIBUTE}="" x1="5" y1="0" x2="5" y2="80"></line>
    <circle class="axis" style="display: none" cx="5" cy="5" r="2"></circle>`
  document.body.appendChild(svg)
  return svg
}

afterEach(() => {
  document.getElementById('figure-test-style')?.remove()
  document.querySelectorAll('body > svg').forEach((el) => el.remove())
})

describe('standaloneSvg', () => {
  it('inlines computed styles and drops skipped and hidden elements', () => {
    const svg = plot()
    const figure = standaloneSvg(svg, { width: 120, height: 80, background: 'rgb(1, 2, 3)' })
    expect(figure.querySelectorAll('[class]')).toHaveLength(0)
    expect(figure.querySelectorAll('line')).toHaveLength(1)
    expect(figure.querySelector('circle')).toBeNull()
    const line = figure.querySelector('line')!
    expect(line.getAttribute('style')).toContain('stroke:rgb(255, 0, 0)')
    expect(line.getAttribute('style')).toContain('stroke-width:2px')
    const text = figure.querySelector('text')!
    expect(text.getAttribute('style')).toContain('fill:rgb(0, 0, 255)')
    expect(text.getAttribute('style')).toContain('font-size:13px')
    expect(text.getAttribute('style')).toMatch(/font-family:[^;]*Helvetica/)
    expect(figure.firstElementChild?.getAttribute('fill')).toBe('rgb(1, 2, 3)')
    expect(figure.getAttribute('viewBox')).toBe('0 0 120 80')
    // the page is not changed
    expect(svg.querySelectorAll('line')).toHaveLength(2)
  })

  it('serialises to a file and renders a PNG at 2x', async () => {
    const figure = standaloneSvg(plot(), { width: 120, height: 80, background: 'white' })
    const text = await svgText(figure, true)
    expect(text.startsWith('<?xml')).toBe(true)
    expect(text).toContain('xmlns="http://www.w3.org/2000/svg"')
    expect(text).toContain('RT1 (min)')
    const png = await svgToPng(text, 120, 80)
    expect(png.type).toBe('image/png')
    const bitmap = await createImageBitmap(png)
    expect([bitmap.width, bitmap.height]).toEqual([240, 160])
  })
})

describe('withTheme', () => {
  it('reads the styles of the other theme and restores the page', () => {
    const svg = plot()
    const extra = document.createElement('style')
    extra.id = 'figure-test-dark'
    extra.textContent = '.dark .label { fill: rgb(250, 250, 250); transition: fill 1s }'
    document.head.appendChild(extra)
    const before = pageTheme()
    const other = before === 'dark' ? 'light' : 'dark'
    const figure = withTheme('dark', () => standaloneSvg(svg, { width: 120, height: 80 }))
    expect(figure.querySelector('text')!.getAttribute('style')).toContain('fill:rgb(250, 250, 250)')
    expect(pageTheme()).toBe(before)
    expect(withTheme(other, () => pageTheme())).toBe(other)
    expect(pageTheme()).toBe(before)
    extra.remove()
  })
})

describe('figureName', () => {
  it('builds safe file names', () => {
    expect(figureName('run 1.cdf', 'EIC-91', null, 'Toluene')).toBe('run-1_EIC-91_Toluene')
    expect(figureName('Example_FID.csv')).toBe('Example_FID')
  })
})
