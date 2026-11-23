import { afterEach, describe, expect, it } from 'vitest'
import type { QuotesData } from '@/api/trading'
import type { MarketData, SymbolData } from '@/lib/MarketDataManager'
import { setNumberGrouping } from '@/sudarshan/dashboard/numberStyle'
import {
  cellsOf,
  columnsFor,
  FIGURES,
  figureNumber,
  kindOf,
  needsQuote,
  sameCells,
  shownFigures,
} from './figures'
import type { Snapshot } from './rows'
import type { Figure } from './v2/types'

const READ_AT = 1_000
const quote = (over: Partial<QuotesData> = {}): Snapshot => ({
  quote: {
    ltp: 1500,
    prev_close: 1490,
    open: 1495,
    high: 1510,
    low: 1480,
    volume: 1_20_000,
    oi: 0,
    bid: 0,
    ask: 0,
    ...over,
  },
  at: READ_AT,
})
const line = (data: MarketData, at: number): SymbolData => ({
  symbol: 'INFY',
  exchange: 'NSE',
  data,
  lastUpdate: at,
  updateSource: 'websocket',
})

afterEach(() => setNumberGrouping('indian'))

describe('a figure comes from the newer of the snapshot and the tick (M8.3)', () => {
  it.each([
    ['high', { high: 1520 }, 1520],
    ['low', { low: 1470 }, 1470],
    ['open', { open: 1496 }, 1496],
    ['volume', { volume: 2_50_000 }, 2_50_000],
  ] as const)('%s: the tick after the read wins', (id, data, want) => {
    expect(figureNumber(id, quote(), line(data, READ_AT + 1))).toBe(want)
    expect(figureNumber(id, quote(), line(data, READ_AT))).toBe(want) // the indexView rule: at or after
  })

  it.each([
    ['high', { high: 1520 }, 1510],
    ['volume', { volume: 9 }, 1_20_000],
  ] as const)('%s: a tick older than the read does not undo it', (id, data, want) => {
    expect(figureNumber(id, quote(), line(data, READ_AT - 1))).toBe(want)
  })

  it('takes the snapshot when the tick does not carry the field (an LTP frame)', () => {
    expect(figureNumber('high', quote(), line({ ltp: 1501 }, READ_AT + 5))).toBe(1510)
    expect(figureNumber('open', quote(), line({ ltp: 1501 }, READ_AT + 5))).toBe(1495)
  })

  it('takes the previous close as the change line does: a tick’s whenever it holds one', () => {
    expect(figureNumber('prev', quote(), line({ prev_close: 1488 }, READ_AT - 50))).toBe(1488)
    expect(figureNumber('prev', quote(), line({ ltp: 1 }, READ_AT + 5))).toBe(1490)
  })

  it('has ATP and IV from Quote frames only, IV as a percentage', () => {
    expect(figureNumber('atp', quote(), undefined)).toBeNull()
    expect(figureNumber('iv', quote(), undefined)).toBeNull()
    const tick = line({ average_price: 1499.35, iv: 0.1314 }, READ_AT + 1)
    expect(figureNumber('atp', quote(), tick)).toBe(1499.35)
    expect(figureNumber('iv', quote(), tick)).toBeCloseTo(13.14, 6)
  })

  it('reads a price of 0 or less as not known, and a volume or open interest of 0 as 0', () => {
    expect(figureNumber('high', quote({ high: 0 }), undefined)).toBeNull()
    expect(figureNumber('low', quote({ low: -1 }), undefined)).toBeNull()
    expect(figureNumber('open', quote({ open: 0 }), line({ open: 0 }, READ_AT + 1))).toBeNull()
    expect(figureNumber('volume', quote({ volume: 0 }), undefined)).toBe(0)
    expect(figureNumber('oi', quote({ oi: 0 }), undefined)).toBe(0)
    expect(figureNumber('high', undefined, undefined)).toBeNull()
  })
})

describe('what a row draws (M8.3)', () => {
  it('formats prices in the exchange’s decimals, sizes in lakh and crore, IV to one place', () => {
    const tick = line({ oi: 1_23_45_000, iv: 0.1314, average_price: 101.5 }, READ_AT + 1)
    const cells = cellsOf(
      ['high', 'volume', 'oi', 'iv'],
      'NIFTY24NOV2625900CE',
      'NFO',
      quote(),
      tick
    )
    expect(cells.map((cell) => cell.text)).toEqual(['1,510.00', '1.2L', '1.2Cr', '13.1%'])
    expect(cells[0].speech).toBe('Day high 1,510.00')
    expect(
      cellsOf(['high'], 'USDINR26NOVFUT', 'CDS', quote({ high: 83.1234 }), undefined)[0].text
    ).toBe('83.1234')
  })

  it('follows the lakh and crore switch', () => {
    setNumberGrouping('international')
    const cells = cellsOf(['volume'], 'INFY', 'NSE', quote({ volume: 3_40_000 }), undefined)
    expect(cells[0].text).toBe('340k')
  })

  it('is a dash and "not received yet" before a figure arrives', () => {
    const [atp] = cellsOf(['atp'], 'INFY', 'NSE', quote(), undefined)
    expect(atp).toEqual({
      id: 'atp',
      state: 'wait',
      text: null,
      speech: 'Average price not received yet',
    })
  })

  it.each([
    ['oi', 'INFY', 'NSE', 'No open interest for a share'],
    ['oi', 'NIFTY 50', 'NSE_INDEX', 'No open interest for an index'],
    ['volume', 'NIFTY 50', 'NSE_INDEX', 'No volume for an index'],
    ['atp', 'NIFTY 50', 'NSE_INDEX', 'No average price for an index'],
    ['iv', 'NIFTY24NOV26FUT', 'NFO', 'No IV for a future'],
    ['iv', 'INFY', 'NSE', 'No IV for a share'],
  ] as const)('leaves %s blank for %s on %s, and says why', (id, symbol, exchange, speech) => {
    expect(cellsOf([id], symbol, exchange, quote(), undefined)).toEqual([
      { id, state: 'none', text: null, speech },
    ])
  })

  it('tells an instrument by its exchange and symbol', () => {
    expect(kindOf('NIFTY 50', 'NSE_INDEX')).toBe('index')
    expect(kindOf('INFY', 'NSE')).toBe('share')
    expect(kindOf('CRUDEOILM20MAY26FUT', 'MCX')).toBe('future')
    expect(kindOf('NIFTY24NOV2625900PE', 'NFO')).toBe('option')
  })

  it('says two rows are the same when every cell says the same', () => {
    const a = cellsOf(['high', 'low'], 'INFY', 'NSE', quote(), undefined)
    expect(sameCells(a, cellsOf(['high', 'low'], 'INFY', 'NSE', quote(), undefined))).toBe(true)
    expect(
      sameCells(a, cellsOf(['high', 'low'], 'INFY', 'NSE', quote({ low: 1 }), undefined))
    ).toBe(false)
    expect(sameCells(a, a.slice(0, 1))).toBe(false)
    expect(sameCells(undefined, undefined)).toBe(true)
  })
})

describe('how many show, and what they cost (M8.3, W2, T1)', () => {
  it.each([
    [360, 1],
    [412, 1],
    [600, 2],
    [800, 3],
  ])('%i px gives %i column(s) at 16 px and at 18 px to the rem', (width, columns) => {
    expect(columnsFor(width, 16)).toBe(columns)
    expect(columnsFor(width, 18)).toBe(columns)
  })

  it('starts phone first before the list is measured', () => {
    expect(columnsFor(0, 16)).toBe(1)
  })

  it('shows the first two, four or six in the order picked, never more than six', () => {
    const picked: Figure[] = ['iv', 'high', 'low', 'open', 'prev', 'volume', 'oi']
    expect(shownFigures(picked, 1)).toEqual(['iv', 'high'])
    expect(shownFigures(picked, 2)).toEqual(['iv', 'high', 'low', 'open'])
    expect(shownFigures(picked, 3)).toEqual(['iv', 'high', 'low', 'open', 'prev', 'volume'])
    expect(shownFigures(undefined, 3)).toEqual([])
  })

  it('asks for Quote frames only for a figure shown that needs them', () => {
    expect(needsQuote([])).toBe(false)
    expect(needsQuote(['open', 'prev'])).toBe(false)
    expect(needsQuote(['open', 'high'])).toBe(true)
    expect(FIGURES.filter((each) => each.quote).map((each) => each.id)).toEqual([
      'high',
      'low',
      'volume',
      'oi',
      'atp',
      'iv',
    ])
    // A figure hidden at this width costs no data: an upright phone showing open and prev only.
    expect(needsQuote(shownFigures(['open', 'prev', 'high'], 1))).toBe(false)
  })
})
