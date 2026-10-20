import { afterEach, describe, expect, it } from 'vitest'
import {
  CHART_CHIPS,
  chartPath,
  DEFAULT_CHIP,
  DEFAULT_INSTRUMENT,
  isChartChip,
  readChip,
  readLastInstrument,
  saveChip,
  saveLastInstrument,
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

describe('the timeframes', () => {
  it('offers the six the phone chart shows, in order', () => {
    expect(CHART_CHIPS).toEqual(['1m', '3m', '5m', '15m', '1h', '1D'])
    expect(isChartChip('15m')).toBe(true)
    expect(isChartChip('2m')).toBe(false)
    expect(isChartChip(5)).toBe(false)
  })

  it('remembers the last one chosen on this device', () => {
    expect(readChip()).toBe(DEFAULT_CHIP)
    saveChip('15m')
    expect(readChip()).toBe('15m')
  })

  it('falls back to the default for a value an older build saved, or blocked storage', () => {
    localStorage.setItem('sudarshan-chart-interval', '2h')
    expect(readChip()).toBe(DEFAULT_CHIP)
    expect(readChip(refusing)).toBe(DEFAULT_CHIP)
    expect(() => saveChip('1m', refusing)).not.toThrow()
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
