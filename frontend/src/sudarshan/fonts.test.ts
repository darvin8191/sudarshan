/**
 * The self-hosted font layer's contract, read from fonts.css.
 *
 * The CSP allows fonts from this origin only (`font-src 'self'`). Vite inlines
 * an asset under 4 KB as a data: URI, which that policy blocks, so every face
 * must point at a real WOFF2 in the package that is larger than that. Every
 * face also needs a unicode-range, or the browser fetches it to test glyphs it
 * does not have; and weights stay within 400 to 700.
 */
import { existsSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(join(process.cwd(), 'src/sudarshan/fonts.css'), 'utf8')
const faces = [...css.matchAll(/@font-face\s*{([^}]*)}/g)].map((m) => m[1])

function prop(face: string, name: string): string {
  return new RegExp(`${name}:\\s*([^;]+);`).exec(face)?.[1]?.trim() ?? ''
}

const VITE_INLINE_LIMIT = 4096

describe('self-hosted fonts', () => {
  it('declares the four families, in the weights the UI uses', () => {
    const declared = faces.map((f) => `${prop(f, 'font-family')} ${prop(f, 'font-weight')}`).sort()
    expect(declared).toEqual(
      [
        "'Inter' 400",
        "'Inter' 500",
        "'Inter' 600",
        "'Inter' 700",
        "'Noto Sans Devanagari' 400",
        "'Noto Sans Devanagari' 500",
        "'Noto Sans Devanagari' 600",
        "'Noto Sans Devanagari' 700",
        "'Noto Sans Gujarati' 400",
        "'Noto Sans Gujarati' 500",
        "'Noto Sans Gujarati' 600",
        "'Noto Sans Gujarati' 700",
        "'Tiro Devanagari Sanskrit' 400",
      ].sort()
    )
  })

  it.each(
    faces.map((f) => [prop(f, 'src'), f] as const)
  )('%s is a bundled WOFF2 too large to be inlined', (src, face) => {
    const url = /url\('([^']+)'\)\s*format\('woff2'\)/.exec(src)?.[1]
    expect(url, 'one woff2 source').toBeDefined()
    const file = join(process.cwd(), 'node_modules', url as string)
    expect(existsSync(file), file).toBe(true)
    expect(statSync(file).size).toBeGreaterThan(VITE_INLINE_LIMIT)
    expect(prop(face, 'unicode-range')).toMatch(/^U\+/)
    expect(prop(face, 'font-display')).toBe('swap')
  })

  it('fetches nothing from another origin', () => {
    expect(css).not.toMatch(/url\(['"]?(https?:)?\/\//)
  })
})
