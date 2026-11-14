import { describe, expect, it } from 'vitest'
import {
  COMMON_STUDIES,
  hasOpenInterest,
  legendValues,
  MAX_STUDIES,
  type Placement,
  REFUSED,
  refusal,
  type StudyEntry,
  skipReason,
  studyFields,
  VOLUME_MA,
  volumeMaAt,
  volumeMaLength,
} from './phoneStudies'

const entry = (id: string): StudyEntry => ({ id, settings: {} })
const placementOf = (id: string): Placement =>
  COMMON_STUDIES.find((study) => study.id === id)?.placement ?? 'onchart'

describe('the common list', () => {
  it('names what a trader reaches for first, and open interest for a contract', () => {
    expect(COMMON_STUDIES.map((study) => study.label)).toEqual([
      'EMA',
      'SMA',
      'VWAP',
      'Bollinger Bands',
      'Supertrend',
      'RSI',
      'MACD',
      'ATR',
      'ADX',
      'CPR with pivots',
      'Volume moving average',
      'Open interest',
    ])
    expect(COMMON_STUDIES.find((s) => s.id === 'open-interest')?.derivativeOnly).toBe(true)
  })
})

describe('the limits', () => {
  it('takes five, and refuses the sixth in a sentence', () => {
    const four = ['ema', 'sma', 'vwap', 'supertrend'].map(entry)
    expect(refusal(four, 'onchart', placementOf)).toBeNull()
    const five = [...four, entry('cpr')]
    expect(five).toHaveLength(MAX_STUDIES)
    expect(refusal(five, 'onchart', placementOf)).toBe('count')
    expect(REFUSED.count).toBe(
      'Five indicators at most on the phone chart. Remove one, or open the full terminal.'
    )
  })

  it('takes two in a pane of their own, refuses a third, and still takes an overlay', () => {
    const two = [entry('rsi'), entry('macd')]
    expect(refusal(two, 'pane', placementOf)).toBe('panes')
    expect(refusal(two, 'onchart', placementOf)).toBeNull()
    expect(refusal(two, 'host', placementOf)).toBeNull()
    expect(refusal([entry('rsi')], 'pane', placementOf)).toBeNull()
  })

  it('takes the same study twice, as two averages of different lengths', () => {
    expect(refusal([entry('ema')], 'onchart', placementOf)).toBeNull()
  })
})

describe('what an instrument cannot draw', () => {
  const ema = { name: 'EMA', placement: 'onchart' as const, category: 'Trend' }
  const oi = { name: 'Open Interest', placement: 'pane' as const, category: 'Volume' }
  const vwap = { name: 'VWAP', placement: 'onchart' as const, category: 'Volume' }
  const index = { exchange: 'NSE_INDEX', traded: false }
  const share = { exchange: 'NSE', traded: true }
  const contract = { exchange: 'NFO', traded: true }

  it('skips open interest where there is none, with a line saying so', () => {
    expect(skipReason('open-interest', oi, contract)).toBeNull()
    expect(skipReason('open-interest', oi, share)).toBe(
      'Open Interest is not shown: this instrument has no open interest.'
    )
    expect(hasOpenInterest('MCX')).toBe(true)
    expect(hasOpenInterest('NSE_INDEX')).toBe(false)
  })

  it('skips a volume study and the volume average on an index, which has no volume', () => {
    expect(skipReason('vwap', vwap, index)).toBe('VWAP is not shown: an index has no volume.')
    expect(skipReason(VOLUME_MA, null, index)).toBe(
      'Volume moving average is not shown: an index has no volume.'
    )
    expect(skipReason(VOLUME_MA, null, share)).toBeNull()
    expect(skipReason('ema', ema, index)).toBeNull()
  })

  it('says so when a saved study is not on the server any more', () => {
    expect(skipReason('my-script', null, share)).toBe(
      'An indicator in the saved set is not on this server any more.'
    )
  })
})

describe('the legend values', () => {
  it("writes a price at the instrument's decimals in en-IN, and a gap as a dash", () => {
    const columns = { value: [null, 25_912.3456, Number.NaN] }
    expect(legendValues(columns, ['value'], 1, 2)).toEqual(['25,912.35'])
    expect(legendValues(columns, ['value'], 0, 2)).toEqual(['—'])
    expect(legendValues(columns, ['value'], 2, 2)).toEqual(['—'])
    expect(legendValues(columns, ['missing'], 1, 2)).toEqual(['—'])
  })
})

describe('the volume average', () => {
  it('averages the length it was given, after its warmup', () => {
    const volumes = [10, 20, 30, undefined, 50]
    expect(volumeMaAt(volumes, 1, 3)).toBeNull()
    expect(volumeMaAt(volumes, 2, 3)).toBe(20)
    expect(volumeMaAt(volumes, 4, 3)).toBeCloseTo((30 + 0 + 50) / 3)
  })

  it('reads a damaged or cleared length as twenty', () => {
    expect(volumeMaLength({ length: 9 })).toBe(9)
    expect(volumeMaLength({ length: '' })).toBe(20)
    expect(volumeMaLength({ length: 1 })).toBe(20)
    expect(volumeMaLength({})).toBe(20)
  })
})

describe("a study's settings on the phone", () => {
  const sources = [
    { label: 'Close', value: 'close' },
    { label: 'Open', value: 'open' },
  ]

  it('draws numbers, switches, choices and text, a price source as a choice, and leaves colours out', () => {
    const fields = studyFields(
      [
        { key: 'length', type: 'number', label: 'Length', default: 9, min: 1, max: 500, step: 1 },
        { key: 'source', type: 'source', label: 'Source', default: 'close' },
        { key: 'color', type: 'color', label: 'Color', default: '#4f8cff' },
        {
          key: 'showBand',
          type: 'boolean',
          label: 'Show band',
          default: true,
          tooltip: 'The band',
        },
        {
          key: 'mode',
          type: 'select',
          label: 'Mode',
          default: 'a',
          options: [{ label: 'A', value: 'a' }, 'b'],
          visibleWhen: { key: 'showBand', is: true },
        },
        { key: 'timeframe', type: 'interval', label: 'Timeframe', default: '' },
        { key: 'session', type: 'session', label: 'Session', default: '0915-1530' },
        { type: 'number' },
      ],
      sources
    )
    expect(fields.map((f) => [f.key, f.type])).toEqual([
      ['length', 'number'],
      ['source', 'select'],
      ['showBand', 'boolean'],
      ['mode', 'select'],
      ['timeframe', 'interval'],
    ])
    expect(fields[0]).toMatchObject({ min: 1, max: 500, step: 1 })
    expect(fields[1].options).toBe(sources)
    expect(fields[2].tooltip).toBe('The band')
    expect(fields[3].options).toEqual([
      { label: 'A', value: 'a' },
      { label: 'b', value: 'b' },
    ])
    expect(fields[3].visibleWhen).toEqual({ key: 'showBand', is: true })
  })
})
