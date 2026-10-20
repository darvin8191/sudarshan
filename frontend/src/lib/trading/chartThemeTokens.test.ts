/**
 * The light candle pair is the app's `--up` / `--down` tokens, so the chart has
 * no palette of its own and colour-blind mode's blue and orange reach the
 * candles. A build without those tokens keeps the spec pair rather than
 * painting candles in the ink an undefined var() would resolve to.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { buildChartTheme, CANDLE_PAIRS } from './chartTheme'

const root = document.documentElement

afterEach(() => {
  root.style.removeProperty('--up')
  root.style.removeProperty('--down')
})

describe('candle colours from the app tokens', () => {
  it('falls back to the spec pair when no stylesheet defines the tokens', () => {
    const theme = buildChartTheme('light', 'live')

    expect(theme.upColor).toBe(CANDLE_PAIRS.light.up)
    expect(theme.downColor).toBe(CANDLE_PAIRS.light.down)
    expect(CANDLE_PAIRS.light).toEqual({ up: '#007e3f', down: '#c9302d' })
  })

  it('reads --up and --down when they are defined', () => {
    root.style.setProperty('--up', '#1c65c8')
    root.style.setProperty('--down', '#ac5700')

    const theme = buildChartTheme('light', 'live')

    // jsdom has no canvas to rasterise with, so the token reference itself is
    // what comes back; a browser hands the canvas the resolved rgb().
    expect(theme.upColor).toBe('var(--up)')
    expect(theme.downColor).toBe('var(--down)')
    expect(theme.lastPriceUp).toBe(theme.upColor)
    expect(theme.lastPriceDown).toBe(theme.downColor)
  })
})
