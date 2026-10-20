import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileMenuItems } from '@/config/navigation'

/**
 * The phone chart is not done until all three registrations are present
 * (CLAUDE.md, "Adding a page"), and it speaks the app's words for the feed.
 */
const SRC = join(process.cwd(), 'src')
const read = (...path: string[]) => readFileSync(join(...path), 'utf8')

describe('the phone chart is registered three times', () => {
  it('has its routes in App.tsx, inside the full-width layout', () => {
    const app = read(SRC, 'App.tsx')
    expect(app).toContain("const PhoneChart = lazy(() => import('@/sudarshan/chart/ChartPage'))")
    const fullWidth = app.split('<Route element={<FullWidthLayout />}>')[1]?.split('</Route>')[0]
    expect(fullWidth).toContain('<Route path="/chart" element={<PhoneChart />} />')
    expect(fullWidth).toContain(
      '<Route path="/chart/:exchange/:symbol" element={<PhoneChart />} />'
    )
  })

  it('has the same paths in react_app.py, so a refresh is a known route', () => {
    const flask = read(SRC, '..', '..', 'blueprints', 'react_app.py')
    expect(flask).toContain('@react_bp.route("/chart", strict_slashes=False)')
    expect(flask).toContain('@react_bp.route("/chart/<exchange>/<symbol>", strict_slashes=False)')
  })

  it('has an entry in the profile menu', () => {
    expect(profileMenuItems.find((item) => item.href === '/chart')?.label).toBe('Chart')
  })
})

describe('the words the phone chart uses', () => {
  const dir = join(SRC, 'sudarshan', 'chart')
  const files = readdirSync(dir).filter(
    (name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)
  )
  /** Quoted strings and JSX text, with the comments taken out. */
  const visible = (source: string) => {
    const code = source
      .replace(/\/\*[\s\S]*?\*\//g, ' ')
      .split('\n')
      .map((line) => line.replace(/\/\/.*$/, ''))
      .join('\n')
    return [
      ...(code.match(/'[^'\n]*'|"[^"\n]*"|`[^`]*`/g) ?? []),
      ...(code.match(/>[^<>{}]+</g) ?? []),
    ].filter((text) => text.includes(' '))
  }

  it('reads every source file in the folder', () => {
    expect(files.length).toBeGreaterThanOrEqual(7)
  })

  it.each([
    ['"Live" for the feed: it says "Real-time"', /\blive\b/i],
    ['"arm", "armed" or "arming"', /\barm(ed|ing)?\b/i],
    ['"paper trading" or "virtual trading"', /\b(paper|virtual) trading\b/i],
    ['a status code', /\b(HTTP|status code)\b|\b[45]\d\d\b/],
  ])('never shows %s', (_what, word) => {
    const found = files.flatMap((name) =>
      visible(read(dir, name))
        .filter((text) => word.test(text))
        .map((text) => `${name}: ${text}`)
    )
    expect(found).toEqual([])
  })
})
