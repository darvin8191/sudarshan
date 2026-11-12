/**
 * The token layer's contract, read straight from theme.css.
 *
 * Each token is written in oklch() with the design spec's hex in a comment
 * beside it. The two can drift apart silently, since nothing renders the
 * comment, so this converts every value back to sRGB and requires the hex it
 * claims. It then requires every text token to clear 4.5:1 on the page, the
 * rule the owner set for reading a phone in sunlight, every chip's text to
 * clear it on the chip's own fill, and every outline that has to be seen to
 * clear 3:1.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(join(process.cwd(), 'src/sudarshan/theme.css'), 'utf8')

const CANVAS = '#F4F6F8'
const WHITE = '#FFFFFF'

/** Tokens that colour text, which must read at 4.5:1 or better on the canvas. */
const TEXT_TOKENS = [
  '--text-primary',
  '--text-secondary',
  '--text-tertiary',
  '--brand-hover',
  '--gold-ink',
  '--up',
  '--down',
  '--buy-solid',
  '--sell-solid',
  '--profit-strong',
  '--loss-strong',
  '--neutral',
  '--stale',
  '--brand',
  '--family',
  '--sandbox',
  '--sandbox-ink',
  '--live-ink',
  '--warning-text',
  '--info',
  '--up-cvd',
  '--down-cvd',
  '--destructive',
]

function toLinear(channel: number): number {
  const c = channel / 255
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function luminance(hex: string): number {
  const n = Number.parseInt(hex.slice(1), 16)
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(toLinear)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** CSS Color 4 OKLCH to an sRGB hex, the conversion a browser performs. */
function oklchToHex(l: number, c: number, h: number): string {
  const a = c * Math.cos((h * Math.PI) / 180)
  const b = c * Math.sin((h * Math.PI) / 180)
  const l1 = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m1 = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s1 = (l - 0.0894841775 * a - 1.291485548 * b) ** 3
  const linear = [
    4.0767416621 * l1 - 3.3077115913 * m1 + 0.2309699292 * s1,
    -1.2684380046 * l1 + 2.6097574011 * m1 - 0.3413193965 * s1,
    -0.0041960863 * l1 - 0.7034186147 * m1 + 1.707614701 * s1,
  ]
  return `#${linear
    .map((v) => {
      const x = Math.min(1, Math.max(0, v))
      const srgb = x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055
      return Math.round(srgb * 255)
        .toString(16)
        .padStart(2, '0')
    })
    .join('')
    .toUpperCase()}`
}

/** HSL channels, as upstream writes its trading tokens, to an sRGB hex. */
function hslToHex(h: number, s: number, l: number): string {
  const sat = s / 100
  const light = l / 100
  const k = (n: number) => (n + h / 30) % 12
  const f = (n: number) =>
    light - sat * Math.min(light, 1 - light) * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1))
  return `#${[f(0), f(8), f(4)]
    .map((v) =>
      Math.round(v * 255)
        .toString(16)
        .padStart(2, '0')
    )
    .join('')
    .toUpperCase()}`
}

interface Declared {
  name: string
  hex: string
  claimed: string
}

/** Every `--name: oklch(...) /* #HEX` line in the first :root block. */
function oklchTokens(): Declared[] {
  const root = css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf(':root {')))
  const found: Declared[] = []
  for (const m of root.matchAll(
    /(--[\w-]+):\s*oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\);\s*\/\*\s*(#[0-9A-F]{6})/g
  )) {
    const [, name, l, c, h, claimed] = m
    found.push({ name, hex: oklchToHex(Number(l), Number(c), Number(h)), claimed })
  }
  return found
}

function hslTokens(block: string): Declared[] {
  const found: Declared[] = []
  for (const m of block.matchAll(
    /(--[\w-]+):\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%;\s*\/\*\s*(#[0-9A-F]{6})/g
  )) {
    const [, name, h, s, l, claimed] = m
    found.push({ name, hex: hslToHex(Number(h), Number(s), Number(l)), claimed })
  }
  return found
}

describe('SUDARSHAN tokens', () => {
  const tokens = oklchTokens()
  const byName = new Map(tokens.map((t) => [t.name, t.hex]))

  it('reads the token block', () => {
    expect(tokens.length).toBeGreaterThan(30)
  })

  it.each(tokens.map((t) => [t.name, t] as const))('%s is the hex its comment names', (_, t) => {
    expect(t.hex).toBe(t.claimed)
  })

  it.each(TEXT_TOKENS)('%s reads at 4.5:1 or better on the page', (name) => {
    const hex = byName.get(name)
    expect(hex, `${name} not found in theme.css`).toBeDefined()
    expect(contrast(hex as string, CANVAS)).toBeGreaterThanOrEqual(4.5)
  })

  it('lays white cards on a grey page, never white on white', () => {
    expect(byName.get('--bg-canvas')).toBe(CANVAS)
    expect(byName.get('--surface-card')).toBe(WHITE)
  })

  // Text that sits on a fill of its own: the chips, the selected nav pill, and
  // the white or ink labels on solid buttons and badges.
  it.each([
    ['--live-ink', '--live-tint'],
    ['--sandbox-ink', '--sandbox-tint'],
    ['--gold-ink', '--gold-tint'],
    ['--up', '--up-tint'],
    ['--down', '--down-tint'],
    // Colour-blind mode moves the down chips to an orange tint of their own (M6.9).
    ['--down-cvd', '--down-cvd-tint'],
    ['--brand', '--brand-tint'],
    ['--text-secondary', '--surface-2'],
    ['--text-secondary', '--surface-3'],
  ])('%s reads at 4.5:1 or better on %s', (text, fill) => {
    const fg = byName.get(text)
    const bg = byName.get(fill)
    expect(fg && bg, `${text} or ${fill} not found`).toBeTruthy()
    expect(contrast(fg as string, bg as string)).toBeGreaterThanOrEqual(4.5)
  })

  it.each([
    '--brand',
    '--brand-hover',
    '--up',
    '--down',
    '--sandbox',
    '--destructive',
    '--buy-solid',
    '--sell-solid',
  ])('white text reads at 4.5:1 or better on %s', (fill) => {
    expect(contrast(WHITE, byName.get(fill) as string)).toBeGreaterThanOrEqual(4.5)
  })

  it('takes ink, not white, on the gold warning fill', () => {
    const fill = byName.get('--warning-fill') as string
    expect(contrast(byName.get('--text-primary') as string, fill)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(WHITE, fill)).toBeLessThan(4.5)
  })

  it.each([
    ['--border-strong', CANVAS],
    ['--border-strong', WHITE],
    ['--focus-ring', CANVAS],
    ['--focus-ring', WHITE],
  ])('%s, an outline that must be seen, clears 3:1 on %s', (name, ground) => {
    expect(contrast(byName.get(name) as string, ground)).toBeGreaterThanOrEqual(3)
  })

  it('writes the upstream trading tokens as HSL triplets of the spec colours', () => {
    const triplets = hslTokens(css)
    expect(triplets.map((t) => t.name)).toEqual(
      expect.arrayContaining(['--profit', '--loss', '--buy', '--sell'])
    )
    for (const t of triplets) expect(t.hex, t.name).toBe(t.claimed)
  })

  it('keeps --buy-solid and --sell-solid the same colours as the --buy and --sell triplets', () => {
    const triplets = new Map(hslTokens(css).map((t) => [t.name, t.hex]))
    expect(byName.get('--buy-solid')).toBe(triplets.get('--buy'))
    expect(byName.get('--sell-solid')).toBe(triplets.get('--sell'))
  })

  it('gives colour-blind mode chips that read: every pair its block makes clears 4.5:1', () => {
    const block = /html\[data-cvd='1'\]\s*{([\s\S]*?)}/.exec(css)?.[1] ?? ''
    const swaps = new Map(
      [...block.matchAll(/(--[\w-]+):\s*var\((--[\w-]+)\)/g)].map((m) => [m[1], m[2]])
    )
    expect(swaps.get('--down')).toBe('--down-cvd')
    expect(swaps.get('--down-tint')).toBe('--down-cvd-tint')
    const ink = (name: string) => byName.get(swaps.get(name) ?? name) as string
    // The chips (parts.tsx STATUS_LOOK) as colour-blind mode draws them.
    expect(contrast(ink('--down'), ink('--down-tint'))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(ink('--up'), ink('--up-tint'))).toBeGreaterThanOrEqual(4.5)
  })

  it('keeps --profit and --loss the same colours as --up and --down', () => {
    const triplets = new Map(
      hslTokens(css.slice(0, css.indexOf("html[data-cvd='1']"))).map((t) => [t.name, t.hex])
    )
    expect(triplets.get('--profit')).toBe(byName.get('--up'))
    expect(triplets.get('--loss')).toBe(byName.get('--down'))
  })

  it.each([
    ['--buy', CANVAS],
    ['--sell', CANVAS],
  ])('%s reads at 4.5:1 or better on the page, and white reads on it', (name, ground) => {
    const hex = hslTokens(css).find((t) => t.name === name)?.hex as string
    expect(contrast(hex, ground)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(WHITE, hex)).toBeGreaterThanOrEqual(4.5)
  })

  it('stops motion for reduce-motion devices, spinners excepted (M1.21)', () => {
    const reduced = /@media \(prefers-reduced-motion: reduce\)\s*{([\s\S]*?)\n}/.exec(css)?.[1]
    expect(reduced, 'a prefers-reduced-motion block').toBeDefined()
    expect(reduced).toContain('*:not(.animate-spin)')
    expect(reduced).toMatch(/animation-duration: 0\.01ms !important/)
    expect(reduced).toMatch(/transition-duration: 0\.01ms !important/)
  })

  it('has no dark variant anywhere', () => {
    const rules = css.replace(/\/\*[\s\S]*?\*\//g, '')
    expect(rules).not.toMatch(/\.dark\b/)
    expect(rules).not.toMatch(/prefers-color-scheme:\s*dark/)
  })
})

/**
 * The five marks (M8.4 F4): a glyph and a colour each, always drawn with a name. They are pinned
 * here and are not in the rules file's token table. Colour-blind mode changes none of them: the
 * glyph and the name carry the difference.
 */
const MARKS = [
  ['--mark-1', '#B0246B'],
  ['--mark-2', '#5E7800'],
  ['--mark-3', '#7A3B1D'],
  ['--mark-4', '#00796B'],
  ['--mark-5', '#8E2F8E'],
] as const

function lab(hex: string): [number, number, number] {
  const n = Number.parseInt(hex.slice(1), 16)
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(toLinear)
  const x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
  const y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b
  const z = (0.0193339 * r + 0.119192 * g + 0.9503041 * b) / 1.08883
  const f = (t: number) => (t > 216 / 24389 ? t ** (1 / 3) : ((24389 / 27) * t + 16) / 116)
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))]
}

/** CIE76: the straight distance between two colours in CIELAB. */
function deltaE(a: string, b: string): number {
  const [l1, a1, b1] = lab(a)
  const [l2, a2, b2] = lab(b)
  return Math.hypot(l1 - l2, a1 - a2, b1 - b2)
}

describe('SUDARSHAN marks', () => {
  const tokens = oklchTokens()
  const byName = new Map(tokens.map((t) => [t.name, t.hex]))
  // Every colour of the D look, the marks themselves and the upstream chart series left out:
  // the series colours only draw chart lines and are not in the D look's table.
  const others = tokens.filter(
    (t) => !t.name.startsWith('--mark-') && !t.name.startsWith('--chart-')
  )

  it('reads the D look colours it compares against', () => {
    expect(others.length).toBeGreaterThan(40)
  })

  it.each(MARKS)('%s is %s', (name, hex) => {
    expect(byName.get(name)).toBe(hex)
  })

  it.each(MARKS)('%s is 3:1 or better as a graphic on white and on the page', (name) => {
    const hex = byName.get(name) as string
    expect(contrast(hex, WHITE)).toBeGreaterThanOrEqual(3)
    expect(contrast(hex, CANVAS)).toBeGreaterThanOrEqual(3)
  })

  it.each(MARKS)('%s is 20 CIELAB units or more from every D look colour', (name) => {
    const hex = byName.get(name) as string
    for (const other of others) {
      expect(deltaE(hex, other.hex), `${name} against ${other.name}`).toBeGreaterThanOrEqual(20)
    }
  })

  it('keeps the five marks apart from each other too', () => {
    for (const [a, hexA] of MARKS) {
      for (const [b, hexB] of MARKS) {
        if (a < b) expect(deltaE(hexA, hexB), `${a} against ${b}`).toBeGreaterThanOrEqual(20)
      }
    }
  })

  it('leaves the marks alone in colour-blind mode', () => {
    const block = /html\[data-cvd='1'\]\s*{([\s\S]*?)}/.exec(css)?.[1] ?? ''
    expect(block).not.toMatch(/--mark-/)
  })
})
