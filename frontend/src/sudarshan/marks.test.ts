/**
 * The inline marks are copies of the SVG files the icon renderer and the chart
 * read. Nothing would show the two disagreeing until somebody compared the app
 * bar with the home-screen icon, so this compares them on every run.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ICON, WORDMARK } from './marks'

const read = (file: string) => readFileSync(join(process.cwd(), 'public/images', file), 'utf8')

describe('brand marks', () => {
  it('draws the icon exactly as public/images/sudarshan-icon.svg', () => {
    const svg = read('sudarshan-icon.svg')

    expect(svg).toContain(`viewBox="${ICON.viewBox}"`)
    expect(svg).toContain(
      `<rect id="tile" width="512" height="512" rx="${ICON.tileRadius}" fill="${ICON.tile}"/>`
    )
    expect(svg).toContain(
      `<circle id="ring" cx="${ICON.ring.cx}" cy="${ICON.ring.cy}" r="${ICON.ring.r}" fill="none" stroke="${ICON.ring.color}" stroke-width="${ICON.ring.width}"/>`
    )
    expect(svg).toContain(
      `<path fill="${ICON.glyph.color}" transform="${ICON.glyph.transform}" d="${ICON.glyph.d}"/>`
    )
  })

  it('is option 1, Mor: a peacock tile, a gold ring and a white glyph', () => {
    expect([ICON.tile, ICON.ring.color, ICON.glyph.color]).toEqual([
      '#0E5A73',
      '#E3A21A',
      '#FFFFFF',
    ])
  })

  it('draws the wordmark exactly as public/images/sudarshan-wordmark.svg', () => {
    const svg = read('sudarshan-wordmark.svg')

    expect(svg).toContain(`viewBox="${WORDMARK.viewBox}"`)
    expect(svg).toContain(`d="${WORDMARK.d}"`)
  })
})
