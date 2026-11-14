/**
 * Profile colours on each of the three looks: the shipped defaults are tuned
 * for a dark chart, so on the light look and in the analyzer look the ones
 * that would vanish are taken from the theme, and nothing else moves.
 */
import type { Bar, PrimitiveRenderContext } from 'openalgo-charts'
import { describe, expect, it, vi } from 'vitest'
import { createChartProfile, profileContrast, profileLook } from './chartProfiles'
import { type ProfileSettings, readProfileSettings } from './profileSettings'

/** The canvas colours of the three looks: the page, and the axis text on it. */
const LOOKS = {
  light: { background: 'rgb(255,255,255)', axisText: 'rgb(71,85,105)' },
  analyzer: { background: 'rgb(255,255,255)', axisText: 'rgb(82,45,116)' },
  dark: { background: '#101418', axisText: '#b8c0cc' },
}
const KINDS = ['tpo', 'session-volume-profile'] as const
const TEXT = new Set(['valuesColor', 'volumeValuesColor'])

const colours = (settings: ProfileSettings) =>
  Object.entries(settings).filter(
    (entry): entry is [string, string] => /Color\d?$/.test(entry[0]) && typeof entry[1] === 'string'
  )
const shipped = (kind: (typeof KINDS)[number]) =>
  readProfileSettings(kind as 'tpo', {}) as ProfileSettings

describe('profile colours on the three looks', () => {
  it('ships defaults some of which cannot be seen on a white chart: the defect this fixes', () => {
    const faint = KINDS.flatMap((kind) =>
      colours(shipped(kind)).filter(
        ([, colour]) => (profileContrast(colour, LOOKS.light.background) ?? 0) < 3
      )
    ).map(([key]) => key)
    expect(faint).toEqual(expect.arrayContaining(['poorHighLowColor', 'valuesColor']))
  })

  for (const look of ['light', 'analyzer'] as const)
    for (const kind of KINDS)
      it(`gives every ${kind} colour its contrast on the ${look} look`, () => {
        for (const [key, colour] of colours(profileLook(shipped(kind), LOOKS[look]))) {
          const contrast = profileContrast(colour, LOOKS[look].background) ?? 0
          expect(contrast, `${key} ${colour}`).toBeGreaterThanOrEqual(TEXT.has(key) ? 4.5 : 3)
        }
      })

  it('writes the volume figures in the axis text of the look, the violet one in the analyzer', () => {
    expect(profileLook(shipped('session-volume-profile'), LOOKS.light)).toMatchObject({
      valuesColor: LOOKS.light.axisText,
    })
    expect(profileLook(shipped('tpo'), LOOKS.analyzer)).toMatchObject({
      volumeValuesColor: LOOKS.analyzer.axisText,
    })
  })

  it('keeps the four period colours four, and leaves a colour that already reads as it is', () => {
    const before = shipped('tpo') as unknown as Record<string, string>
    const after = profileLook(shipped('tpo'), LOOKS.light) as unknown as Record<string, string>
    const gradient = [1, 2, 3, 4].map((n) => after[`gradientColor${n}`])
    expect(new Set(gradient).size).toBe(4)
    // The blue already clears 3:1 on white: untouched.
    expect(after.gradientColor1).toBe(before.gradientColor1)
    expect(after.poorHighLowColor).not.toBe(before.poorHighLowColor)
  })

  it('changes nothing on the dark look, which the defaults were tuned for', () => {
    for (const kind of KINDS) {
      const settings = shipped(kind)
      expect(profileLook(settings, LOOKS.dark)).toBe(settings)
    }
  })

  it('never touches a colour the trader chose, however faint, nor a theme it cannot read', () => {
    const own = {
      ...shipped('session-volume-profile'),
      valuesColor: '#fafafa',
      pocColor: '#fff8c4',
    }
    const look = profileLook(own as ProfileSettings, LOOKS.light) as unknown as Record<
      string,
      string
    >
    expect(look.valuesColor).toBe('#fafafa')
    expect(look.pocColor).toBe('#fff8c4')
    const settings = shipped('tpo')
    expect(profileLook(settings, { background: 'chartreuse-ish', axisText: '#000' })).toBe(settings)
    expect(profileLook(settings, {} as never)).toBe(settings)
  })
})

describe('a profile painted on a look', () => {
  const bars: Bar[] = Array.from({ length: 6 }, (_, i) => ({
    time: Date.parse('2026-09-01T03:45:00Z') / 1000 + i * 60,
    open: 100 + i,
    high: 102 + i,
    low: 99 + i,
    close: 101 + i,
    volume: 500 + i * 100,
  }))
  function paint(theme: Record<string, string>) {
    const texts: Array<{ text: string; color: string }> = []
    const ctx = new Proxy(
      {
        fillStyle: '',
        strokeStyle: '',
        globalAlpha: 1,
        fillText(text: string) {
          texts.push({ text, color: String(this.fillStyle) })
        },
        measureText: (text: string) => ({ width: text.length * 5 }),
      } as Record<string, unknown>,
      { get: (target, key: string) => (key in target ? target[key] : vi.fn()) }
    )
    const rc = {
      dpr: 1,
      plotWidth: 600,
      plotHeight: 400,
      priceAxisWidth: 50,
      timeScale: { barSpacing: 80, indexToX: (index: number) => index * 80 },
      priceScale: { priceToY: (price: number) => 380 - (price - 99) * 40, format: String },
      dataLayer: {
        timeToIndex: (stamp: number) => {
          const i = bars.findIndex((b) => b.time === stamp)
          return i < 0 ? undefined : i
        },
      },
      theme,
    } as unknown as PrimitiveRenderContext
    const profile = createChartProfile(
      'session-volume-profile',
      { ...readProfileSettings('session-volume-profile', {}), showValues: true },
      { tickSize: 1, exchange: 'NSE', timezone: 'Asia/Kolkata', intervalSeconds: 60 }
    )
    profile.setBars(bars)
    profile.draw(ctx as unknown as CanvasRenderingContext2D, rc)
    return texts
  }

  it('writes its volume figures in the axis text on the light look, and as shipped on the dark', () => {
    const light = paint(LOOKS.light)
    expect(light.length).toBeGreaterThan(0)
    expect(new Set(light.map((text) => text.color))).toEqual(new Set([LOOKS.light.axisText]))
    const dark = paint(LOOKS.dark)
    expect(new Set(dark.map((text) => text.color))).toEqual(new Set(['#d1d5db']))
  })
})
