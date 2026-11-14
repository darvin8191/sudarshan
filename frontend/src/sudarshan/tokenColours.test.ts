import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * `--profit` and `--loss` are upstream's HSL channel triplets, not colours. Written as a colour
 * (`text-[color:var(--loss)]`) the browser drops the declaration silently and the text shows in
 * ink, so a loss reads like any other line. SUDARSHAN screens colour up and down from `--up` and
 * `--down`, which also follow the colour-blind mode.
 */
const ROOT = join(process.cwd(), 'src/sudarshan')
const TRIPLET = /color:\s*var\(--(?:profit|loss)\)/g

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return sources(path)
    return /\.(tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : []
  })
}

describe('SUDARSHAN screens write no channel triplet as a colour', () => {
  it('finds the screens', () => {
    expect(sources(ROOT).length).toBeGreaterThan(50)
  })

  it('uses --up and --down, never var(--profit) or var(--loss), as a colour', () => {
    const found = sources(ROOT).flatMap((path) =>
      (readFileSync(path, 'utf8').match(TRIPLET) ?? []).map(
        (hit) => `${relative(ROOT, path)}: ${hit}`
      )
    )
    expect(found).toEqual([])
  })
})
