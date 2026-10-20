/**
 * The token layer's contract, read straight from theme.css.
 *
 * Each token is written in oklch() with the design spec's hex in a comment
 * beside it. The two can drift apart silently, since nothing renders the
 * comment, so this converts every value back to sRGB and requires the hex it
 * claims. It then requires every text token to clear 4.5:1 on the canvas,
 * the rule the owner set for reading a phone in sunlight.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(join(process.cwd(), 'src/sudarshan/theme.css'), 'utf8')

const CANVAS = '#F8F7F3'

/** Tokens that colour text, which must read at 4.5:1 or better on the canvas. */
const TEXT_TOKENS = [
  '--text-primary',
  '--text-secondary',
  '--text-tertiary',
  '--up',
  '--down',
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

  it.each(TEXT_TOKENS)('%s reads at 4.5:1 or better on the canvas', (name) => {
    const hex = byName.get(name)
    expect(hex, `${name} not found in theme.css`).toBeDefined()
    expect(contrast(hex as string, CANVAS)).toBeGreaterThanOrEqual(4.5)
  })

  it('keeps the canvas off pure white', () => {
    expect(byName.get('--bg-canvas')).toBe(CANVAS)
  })

  it('writes the upstream trading tokens as HSL triplets of the spec colours', () => {
    const triplets = hslTokens(css)
    expect(triplets.map((t) => t.name)).toEqual(
      expect.arrayContaining(['--profit', '--loss', '--buy', '--sell'])
    )
    for (const t of triplets) expect(t.hex, t.name).toBe(t.claimed)
  })

  it('has no dark variant anywhere', () => {
    const rules = css.replace(/\/\*[\s\S]*?\*\//g, '')
    expect(rules).not.toMatch(/\.dark\b/)
    expect(rules).not.toMatch(/prefers-color-scheme:\s*dark/)
  })
})
