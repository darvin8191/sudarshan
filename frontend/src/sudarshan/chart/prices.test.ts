import { describe, expect, it, vi } from 'vitest'
import type { LiveStatus } from '@/lib/trading/liveStatus'
import type { PhoneTick } from './phoneChart'
import { PriceStore, tickLine } from './prices'

/** UTC seconds for a time of day in India on Tuesday 20 October 2026. */
const ist = (h: number, mi: number, s = 0) => Date.UTC(2026, 9, 20, h, mi, s) / 1000 - 19_800

const tick = (
  ltp: number,
  polled: boolean,
  time: number | null = null,
  tradeTime: number | null = null
): PhoneTick => ({
  ltp,
  time,
  tradeTime,
  at: (time ?? ist(10, 0)) * 1000,
  polled,
})

const status = (line: LiveStatus['line'], opensAt: number | null = null): LiveStatus => ({
  line,
  lastTickAt: null,
  lastUpdateAt: null,
  lastTickTime: null,
  staleAfterMs: 15_000,
  opensAt,
})

describe('PriceStore', () => {
  it('tells its readers about every price, and keeps the last one the stream sent', () => {
    const store = new PriceStore()
    const heard = vi.fn()
    const off = store.subscribe(heard)
    expect(store.get()).toEqual({ tick: null, streamed: null })

    const first = tick(100, false, ist(10, 0, 1))
    store.set(first)
    // A quote standing in for the stream changes the price, not the last tick.
    const quote = tick(100.5, true)
    store.set(quote)
    expect(store.get()).toEqual({ tick: quote, streamed: first })
    expect(heard).toHaveBeenCalledTimes(2)

    off()
    store.set(tick(101, false))
    expect(heard).toHaveBeenCalledTimes(2)
  })

  it('hands back the same object until something changes, as a store for React must', () => {
    const store = new PriceStore()
    store.set(tick(100, false))
    expect(store.get()).toBe(store.get())
  })
})

describe('tickLine', () => {
  it("gives the exchange's time of the last tick, in India time", () => {
    expect(tickLine(tick(100, false, ist(10, 15, 32)), status({ kind: 'live' }))).toBe(
      'Last tick 10:15:32'
    )
  })

  it('falls back to when the tick arrived when the feed sent no time', () => {
    const arrived: PhoneTick = {
      ltp: 100,
      time: null,
      tradeTime: null,
      at: ist(11, 2, 3) * 1000,
      polled: false,
    }
    expect(tickLine(arrived, null)).toBe('Last tick 11:02:03')
  })

  it('names the last trade where the feed says when it was, so a moving book is not taken for trading', () => {
    const book = tick(100, false, ist(10, 42, 20), ist(10, 31, 2))
    expect(tickLine(book, status({ kind: 'live' }))).toBe('Last trade 10:31:02')
    // An index is computed, not traded: its line is the last tick.
    const index = tick(25_000, false, ist(10, 42, 20), ist(10, 42, 20))
    expect(tickLine(index, status({ kind: 'live' }), Date.now(), false)).toBe('Last tick 10:42:20')
  })

  it('says when a closed market opens, or that the prices are from the last session', () => {
    const now = ist(18, 0) * 1000
    expect(tickLine(null, status({ kind: 'closed' }, ist(9, 15) + 86_400), now)).toBe(
      'Opens Wed 09:15'
    )
    expect(tickLine(null, status({ kind: 'closed' }, ist(9, 15)), ist(8, 0) * 1000)).toBe(
      'Opens at 09:15'
    )
    expect(tickLine(null, status({ kind: 'closed' }))).toBe('Prices from the last session')
    // A snapshot the feed sends after hours does not displace when it opens.
    const evening = tick(100, false, ist(19, 30, 3))
    expect(tickLine(evening, status({ kind: 'closed' }, ist(9, 15) + 86_400), now)).toBe(
      'Opens Wed 09:15'
    )
  })

  it('waits for the first tick in an open market', () => {
    expect(
      tickLine(null, status({ kind: 'connecting', at: null, stale: false, again: false }))
    ).toBe('Waiting for the first tick')
  })
})
