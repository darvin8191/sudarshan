import { afterEach, describe, expect, it } from 'vitest'
import {
  chartPath,
  DEFAULT_INSTRUMENT,
  DEFAULT_INTERVAL,
  DEFAULT_PINNED,
  intervalLabel,
  intervalName,
  isCalendarInterval,
  isInterval,
  MAX_PINNED,
  normalizeInterval,
  readInterval,
  readLastInstrument,
  readPinned,
  saveInterval,
  saveLastInstrument,
  savePinned,
  togglePin,
} from './prefs'

const refusing = {
  getItem(): string | null {
    throw new Error('storage is blocked')
  },
  setItem(): void {
    throw new Error('storage is blocked')
  },
}

afterEach(() => {
  localStorage.clear()
})

describe('a timeframe', () => {
  it('is what a broker names one: minutes, hours, seconds, a day, a week or a month', () => {
    for (const ok of ['1m', '2m', '15m', '30m', '1h', '4h', '30s', 'D', 'W', 'M'])
      expect(isInterval(ok)).toBe(true)
    for (const bad of ['', '0m', '1d', '1D', 'm', '5', '15 m', 5, null, '1000m'])
      expect(isInterval(bad)).toBe(false)
  })

  it("reads an older build's 1D as the broker's D", () => {
    expect(normalizeInterval('1D')).toBe('D')
    expect(normalizeInterval('W')).toBe('W')
    expect(normalizeInterval('day')).toBeNull()
  })

  it('has a short chip and words, a month never told from a minute by case alone in words', () => {
    expect(['1m', '15m', '1h', 'D', 'W', 'M'].map(intervalLabel)).toEqual([
      '1m',
      '15m',
      '1h',
      '1D',
      '1W',
      '1M',
    ])
    expect(['1m', '15m', '1h', '2h', '30s', 'D', 'W', 'M'].map(intervalName)).toEqual([
      '1 minute',
      '15 minutes',
      '1 hour',
      '2 hours',
      '30 seconds',
      '1 day',
      '1 week',
      '1 month',
    ])
    expect(isCalendarInterval('D')).toBe(true)
    expect(isCalendarInterval('1h')).toBe(false)
  })
})

describe('the timeframe last chosen', () => {
  it('is remembered on this device, any the broker offers', () => {
    expect(readInterval()).toBe(DEFAULT_INTERVAL)
    saveInterval('2h')
    expect(readInterval()).toBe('2h')
  })

  it("takes an older build's 1D, and falls back on anything else or on blocked storage", () => {
    localStorage.setItem('sudarshan-chart-interval', '1D')
    expect(readInterval()).toBe('D')
    localStorage.setItem('sudarshan-chart-interval', 'two hours')
    expect(readInterval()).toBe(DEFAULT_INTERVAL)
    expect(readInterval(refusing)).toBe(DEFAULT_INTERVAL)
    expect(() => saveInterval('1m', refusing)).not.toThrow()
  })
})

describe('the pinned timeframes', () => {
  it('start as the six the phone chart always offered, short to long', () => {
    expect(readPinned()).toEqual(['1m', '3m', '5m', '15m', '1h', 'D'])
    expect(DEFAULT_PINNED).toHaveLength(MAX_PINNED)
  })

  it('are kept short to long, each once, six at most, whatever was saved', () => {
    savePinned(['D', '5m', '5m', '1D', '2h'])
    expect(readPinned()).toEqual(['5m', '2h', 'D'])
    localStorage.setItem(
      'sudarshan-chart-pinned',
      JSON.stringify(['1m', '2m', '3m', '5m', '10m', '15m', '30m', 'nonsense'])
    )
    expect(readPinned()).toEqual(['1m', '2m', '3m', '5m', '10m', '15m'])
  })

  it('fall back to the six for a damaged entry, an empty one, or blocked storage', () => {
    localStorage.setItem('sudarshan-chart-pinned', 'not json')
    expect(readPinned()).toEqual([...DEFAULT_PINNED])
    localStorage.setItem('sudarshan-chart-pinned', '["junk"]')
    expect(readPinned()).toEqual([...DEFAULT_PINNED])
    expect(readPinned(refusing)).toEqual([...DEFAULT_PINNED])
    expect(() => savePinned(['1m'], refusing)).not.toThrow()
  })

  it('take a pin in its place by length, and give one up', () => {
    expect(togglePin(['1m', 'D'], '1h')).toEqual({ pinned: ['1m', '1h', 'D'] })
    expect(togglePin(['1m', '1h', 'D'], '1h')).toEqual({ pinned: ['1m', 'D'] })
  })

  it('refuse a seventh, and refuse to give up the last', () => {
    expect(togglePin([...DEFAULT_PINNED], '2h')).toEqual({
      pinned: [...DEFAULT_PINNED],
      refused: 'full',
    })
    expect(togglePin(['5m'], '5m')).toEqual({ pinned: ['5m'], refused: 'last' })
  })
})

describe('the last instrument', () => {
  it('opens NIFTY on a phone that has opened nothing yet', () => {
    expect(readLastInstrument()).toEqual(DEFAULT_INSTRUMENT)
  })

  it('remembers the last instrument opened', () => {
    saveLastInstrument('SBIN', 'NSE')
    expect(readLastInstrument()).toEqual({ symbol: 'SBIN', exchange: 'NSE' })
  })

  it('ignores a damaged entry and blocked storage', () => {
    localStorage.setItem('sudarshan-chart-last', '{"symbol":"","exchange":"NSE"}')
    expect(readLastInstrument()).toEqual(DEFAULT_INSTRUMENT)
    localStorage.setItem('sudarshan-chart-last', 'not json')
    expect(readLastInstrument()).toEqual(DEFAULT_INSTRUMENT)
    expect(readLastInstrument(refusing)).toEqual(DEFAULT_INSTRUMENT)
    expect(() => saveLastInstrument('SBIN', 'NSE', refusing)).not.toThrow()
  })
})

describe('chartPath', () => {
  it('puts the exchange first and escapes what a symbol may contain', () => {
    expect(chartPath('NIFTY', 'NSE_INDEX')).toBe('/chart/NSE_INDEX/NIFTY')
    expect(chartPath('M&M', 'NSE')).toBe('/chart/NSE/M%26M')
    expect(chartPath('NIFTY 50', 'NSE_INDEX')).toBe('/chart/NSE_INDEX/NIFTY%2050')
  })
})
