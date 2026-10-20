/**
 * The watchlist's arithmetic as plain functions: which level a row shows,
 * which previous close it measures the change from (a tick's whenever the
 * tick carries one, the snapshot's otherwise), the direction of the last
 * tick, and how a contract is named on a phone.
 */
import { expect, describe as group, it } from 'vitest'
import type { QuotesData } from '@/api/trading'
import type { SymbolData } from '@/lib/MarketDataManager'
import {
  chainOf,
  describe,
  formatChange,
  priceDecimals,
  QUIET_RESYNC_MS,
  rowStale,
  rowView,
  type Snapshot,
  statusLine,
  tickDirection,
} from './rows'

const T0 = 1_760_000_000_000

function snapshot(ltp: number, prev_close: number, at = T0): Snapshot {
  const quote: QuotesData = {
    ltp,
    prev_close,
    open: 0,
    high: 0,
    low: 0,
    volume: 0,
    oi: 0,
    bid: 0,
    ask: 0,
  }
  return { quote, at }
}

function line(
  ltp: number,
  at: number,
  extra: Record<string, number> = {},
  source: 'websocket' | 'rest' = 'websocket'
): SymbolData {
  return {
    symbol: 'SBIN',
    exchange: 'NSE',
    data: { ltp, ...extra },
    lastUpdate: at,
    updateSource: source,
  }
}

group('rowView', () => {
  it('shows the snapshot until a tick comes, with the change on its previous close', () => {
    const view = rowView(snapshot(812.4, 800), undefined)
    expect(view).toMatchObject({ ltp: 812.4, prevClose: 800, at: T0, live: false })
    expect(view.change).toBeCloseTo(12.4)
    expect(view.changePercent).toBeCloseTo(1.55)
  })

  it('takes the level from a newer tick and keeps the snapshot previous close', () => {
    const view = rowView(snapshot(812.4, 800), line(790, T0 + 1000))
    expect(view).toMatchObject({ ltp: 790, prevClose: 800, live: true })
    expect(view.change).toBeCloseTo(-10)
    expect(view.changePercent).toBeCloseTo(-1.25)
  })

  it('ignores a tick older than the snapshot', () => {
    expect(rowView(snapshot(812.4, 800), line(700, T0 - 1000)).ltp).toBe(812.4)
  })

  it("measures from a tick's prev_close when the tick carries one", () => {
    const view = rowView(snapshot(812.4, 800), line(820, T0 + 1000, { prev_close: 805 }))
    expect(view.prevClose).toBe(805)
    expect(view.change).toBeCloseTo(15)
    expect(view.changePercent).toBeCloseTo(1.86)
  })

  it("prefers a tick's prev_close even when the snapshot read is newer", () => {
    const view = rowView(snapshot(812.4, 800, T0 + 5000), line(820, T0, { prev_close: 795 }))
    expect(view.ltp).toBe(812.4)
    expect(view.prevClose).toBe(795)
    expect(view.change).toBeCloseTo(17.4)
  })

  it("uses the snapshot's previous close when the tick has none, or a zero", () => {
    // Before the completed payloads (M11.3), or a frame that left it out.
    expect(rowView(snapshot(812.4, 800), line(820, T0 + 1000)).prevClose).toBe(800)
    expect(rowView(snapshot(812.4, 800), line(820, T0 + 1000, { prev_close: 0 })).prevClose).toBe(
      800
    )
  })

  it("takes a tick's previous close when there is no snapshot, or it has none", () => {
    expect(rowView(undefined, line(820, T0, { prev_close: 805 })).prevClose).toBe(805)
    expect(
      rowView(snapshot(812.4, 0, T0 + 5000), line(820, T0, { prev_close: 805 })).prevClose
    ).toBe(805)
  })

  it("never measures from the manager's close field, which is not this row's rule", () => {
    // On the completed payloads `close` is also the previous close (0.0 when
    // unknown); before them a Quote frame's close was today's running price.
    // prev_close and the snapshot are the two sources; close is neither.
    const view = rowView(snapshot(812.4, 800), line(820, T0 + 1000, { close: 819 }))
    expect(view.prevClose).toBe(800)
  })

  it('shows no change without a previous close, rather than a confident zero', () => {
    const view = rowView(undefined, line(820, T0))
    expect(view.ltp).toBe(820)
    expect(view.change).toBeNull()
    expect(view.changePercent).toBeNull()
  })

  it('never calls a REST fallback price live', () => {
    expect(rowView(snapshot(812.4, 800), line(815, T0 + 1000, {}, 'rest'))).toMatchObject({
      ltp: 815,
      live: false,
    })
  })
})

group('tickDirection', () => {
  it('is up or down from the level before, and unknown until the level moves', () => {
    expect(tickDirection(null, 100, null)).toBeNull()
    expect(tickDirection(100, 100.05, null)).toBe('up')
    expect(tickDirection(100.05, 99.95, 'up')).toBe('down')
  })

  it('keeps the last direction on a tick at the same level', () => {
    expect(tickDirection(100, 100, 'up')).toBe('up')
    expect(tickDirection(100, 100, 'down')).toBe('down')
    expect(tickDirection(100, null, 'down')).toBe('down')
  })
})

group('describe', () => {
  it('reads an option and a future the way a trader says them', () => {
    expect(describe('NIFTY28OCT2625000CE', 'NFO')).toEqual({
      title: 'NIFTY 25000 CE',
      detail: 'NFO · 28 Oct 2026',
      underlying: 'NIFTY',
    })
    expect(describe('BANKNIFTY25NOV26FUT', 'NFO')).toEqual({
      title: 'BANKNIFTY FUT',
      detail: 'NFO · 25 Nov 2026',
      underlying: 'BANKNIFTY',
    })
    expect(describe('VEDL25APR24292.5PE', 'NFO').title).toBe('VEDL 292.5 PE')
    expect(describe('NIFTYNXT5028OCT26FUT', 'NFO').underlying).toBe('NIFTYNXT50')
    expect(describe('CRUDEOIL19NOV265800CE', 'MCX').title).toBe('CRUDEOIL 5800 CE')
  })

  it('leaves a cash symbol or an index alone, and names the venue', () => {
    expect(describe('RELIANCE', 'NSE')).toEqual({
      title: 'RELIANCE',
      detail: 'NSE',
      underlying: 'RELIANCE',
    })
    expect(describe('NIFTY', 'NSE_INDEX').detail).toBe('NSE INDEX')
    // A share whose name happens to end like a contract is not one.
    expect(describe('ACE', 'NSE').title).toBe('ACE')
  })
})

group('chainOf', () => {
  it('names the underlying and the exchange its options trade on', () => {
    expect(chainOf('NIFTY', 'NSE_INDEX')).toEqual({ exchange: 'NFO', underlying: 'NIFTY' })
    expect(chainOf('SENSEX', 'BSE_INDEX')).toEqual({ exchange: 'BFO', underlying: 'SENSEX' })
    expect(chainOf('RELIANCE', 'NSE')).toEqual({ exchange: 'NFO', underlying: 'RELIANCE' })
    expect(chainOf('NIFTY28OCT2625000CE', 'NFO')).toEqual({
      exchange: 'NFO',
      underlying: 'NIFTY',
    })
    expect(chainOf('CRUDEOIL19NOV26FUT', 'MCX')).toEqual({
      exchange: 'MCX',
      underlying: 'CRUDEOIL',
    })
  })

  it('has none for a segment the option chain tool does not cover', () => {
    expect(chainOf('USDINR28OCT26FUT', 'CDS')).toBeNull()
  })
})

group('formatChange', () => {
  it('signs a change with a plus or a true minus, and a zero with neither', () => {
    expect(formatChange(12.4)).toBe('+12.40')
    expect(formatChange(-1234.5)).toBe('−1,234.50')
    expect(formatChange(0.001)).toBe('0.00')
  })

  it("takes the exchange's decimals", () => {
    expect(formatChange(0.0125, 4)).toBe('+0.0125')
    expect(formatChange(-0.0025, 4)).toBe('−0.0025')
  })
})

group('priceDecimals', () => {
  it('gives the currency segments four decimals and everything else two', () => {
    expect(priceDecimals('CDS')).toBe(4)
    expect(priceDecimals('bcd')).toBe(4)
    expect(priceDecimals('NSE')).toBe(2)
    expect(priceDecimals('NFO')).toBe(2)
    expect(priceDecimals('MCX')).toBe(2)
    expect(priceDecimals('NSE_INDEX')).toBe(2)
  })
})

group('rowStale', () => {
  const old = { ltp: 812.4, at: T0 }
  const later = T0 + 11_000

  it('greys a row only while its market is open and the socket is down', () => {
    expect(rowStale(old, later, true, false)).toBe(true)
    // The socket is up: a quiet row has simply not traded.
    expect(rowStale(old, later, true, true)).toBe(false)
    // Closed or not known: nothing is due.
    expect(rowStale(old, later, false, false)).toBe(false)
    expect(rowStale(old, later, null, false)).toBe(false)
  })

  it('leaves a fresh level, and a row with no level, alone', () => {
    expect(rowStale(old, T0 + 9_000, true, false)).toBe(false)
    expect(rowStale({ ltp: null, at: null }, later, true, false)).toBe(false)
  })

  it('re-reads quiet rows after a minute', () => {
    expect(QUIET_RESYNC_MS).toBe(60_000)
  })
})

group('statusLine', () => {
  const base = { now: T0 + 20_000, marketOpen: true, fallback: false }

  it('says Real-time only while ticks arrive on an open socket', () => {
    expect(
      statusLine({ ...base, lastTickAt: T0 + 19_000, lastUpdateAt: T0 + 19_000, connected: true })
    ).toEqual({ kind: 'live' })
  })

  it('gives a quiet list its age, not Stale, while the socket is up', () => {
    expect(statusLine({ ...base, lastTickAt: T0, lastUpdateAt: T0, connected: true })).toEqual({
      kind: 'delayed',
      at: T0,
      stale: false,
    })
    // Only the snapshot so far: its age, never Real-time.
    expect(statusLine({ ...base, lastTickAt: null, lastUpdateAt: T0, connected: true }).kind).toBe(
      'delayed'
    )
  })

  it('says Reconnecting, stale once old, while the socket is down', () => {
    expect(statusLine({ ...base, lastTickAt: T0, lastUpdateAt: T0, connected: false })).toEqual({
      kind: 'connecting',
      at: T0,
      stale: true,
      again: true,
    })
  })

  it('says Delayed, with the age, while the REST fallback moves the figures', () => {
    expect(
      statusLine({
        ...base,
        lastTickAt: null,
        lastUpdateAt: T0 + 19_000,
        connected: false,
        fallback: true,
      })
    ).toEqual({ kind: 'polling', at: T0 + 19_000, stale: false })
    // After ticks too: the fallback's figures are not ticks.
    expect(
      statusLine({
        ...base,
        lastTickAt: T0 + 2_000,
        lastUpdateAt: T0 + 19_000,
        connected: false,
        fallback: true,
      })
    ).toEqual({ kind: 'polling', at: T0 + 19_000, stale: false })
    // Stale once the fallback's answers stop coming too.
    expect(
      statusLine({ ...base, lastTickAt: null, lastUpdateAt: T0, connected: false, fallback: true })
    ).toEqual({ kind: 'polling', at: T0, stale: true })
    // Nothing in hand yet: still connecting.
    expect(
      statusLine({
        ...base,
        lastTickAt: null,
        lastUpdateAt: null,
        connected: false,
        fallback: true,
      })
    ).toMatchObject({ kind: 'connecting', again: false })
  })

  it('says the market is closed when it is', () => {
    expect(
      statusLine({
        ...base,
        marketOpen: false,
        lastTickAt: null,
        lastUpdateAt: T0,
        connected: true,
      })
    ).toEqual({ kind: 'closed' })
  })
})
