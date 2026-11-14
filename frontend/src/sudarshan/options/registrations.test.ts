import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { menuGroups, profileMenuItems } from '@/config/navigation'

/**
 * The options screen is not done until all three registrations are present (CLAUDE.md, "Adding a
 * page"), and it speaks the app's words for the feed.
 */
const SRC = join(process.cwd(), 'src')
const read = (...path: string[]) => readFileSync(join(...path), 'utf8')

describe('the options screen is registered three times', () => {
  it('has its routes in App.tsx, inside the standard layout', () => {
    const app = read(SRC, 'App.tsx')
    expect(app).toContain(
      "const OptionsPage = lazy(() => import('@/sudarshan/options/OptionsPage'))"
    )
    const standard = app
      .split('<Route element={<Layout />}>')[1]
      ?.split('<Route element={<FullWidthLayout />}>')[0]
    expect(standard).toContain('<Route path="/options" element={<OptionsPage />} />')
    expect(standard).toContain(
      '<Route path="/options/:exchange/:underlying" element={<OptionsPage />} />'
    )
  })

  it('has the same paths in react_app.py, so a refresh is a known route', () => {
    const flask = read(SRC, '..', '..', 'blueprints', 'react_app.py')
    expect(flask).toContain('@react_bp.route("/options", strict_slashes=False)')
    expect(flask).toContain(
      '@react_bp.route("/options/<exchange>/<underlying>", strict_slashes=False)'
    )
  })

  it('has an entry in the profile menu, under Trade, right after the chart there', () => {
    const entry = profileMenuItems.find((item) => item.href === '/options')
    expect(entry).toMatchObject({ label: 'Options', group: 'Trade' })
    const trade = menuGroups(profileMenuItems, { phone: true }).find(
      (section) => section.group === 'Trade'
    )
    const hrefs = trade?.items.map((item) => item.href) ?? []
    expect(hrefs[hrefs.indexOf('/options') - 1]).toBe('/chart')
    // The pins other pages hold are kept: Action Center after API Key, Alerts after Chart.
    const at = (href: string) => profileMenuItems.findIndex((item) => item.href === href)
    expect(at('/action-center')).toBe(at('/apikey') + 1)
    expect(at('/market-alerts')).toBe(at('/chart') + 1)
  })

  it('has its bridge registered in app.py', () => {
    const app = read(SRC, '..', '..', 'app.py')
    expect(app).toContain('app.register_blueprint(sudarshan_options_bp)')
  })
})

describe('the words the options screen uses', () => {
  const dir = join(SRC, 'sudarshan', 'options')
  const files = readdirSync(dir).filter(
    (name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && name !== 'fixtures.ts'
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
    expect(files.length).toBeGreaterThanOrEqual(9)
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

  it('carries no dark styles and no hsl(var()) colour', () => {
    for (const name of files) {
      const source = read(dir, name)
      expect(source, name).not.toMatch(/\bdark:/)
      expect(source, name).not.toMatch(/hsl\(var\(/)
    }
  })
})
