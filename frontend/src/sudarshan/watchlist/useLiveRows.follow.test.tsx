/**
 * F9, the hook: the cap, the window, last ticks kept and the reads, on the market-data test harness
 * (real subscriptions, ticks and frames by hand) and a counted REST read.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const read = vi.hoisted(() => ({ calls: [] as string[][] }))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getMultiQuotes: vi.fn(
      async (_key: string, symbols: Array<{ symbol: string; exchange: string }>) => {
        read.calls.push(symbols.map((each) => `${each.exchange}:${each.symbol}`))
        return {
          status: 'success',
          results: symbols.map((each, index) => ({
            symbol: each.symbol,
            exchange: each.exchange,
            data: {
              ltp: 100 + index,
              prev_close: 100,
              open: 100,
              high: 0,
              low: 0,
              volume: 0,
              oi: 0,
              bid: 0,
              ask: 0,
            },
          })),
        }
      }
    ),
  },
}))

import type { WatchlistItem } from '@/api/watchlist'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { FOLLOW_CAP, WINDOW } from './follow'
import { useLiveRows } from './useLiveRows'

const T0 = Date.UTC(2026, 10, 13, 4, 30) // 10:00 IST

const itemsOf = (count: number): WatchlistItem[] =>
  Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    symbol: `S${index}`,
    exchange: 'NSE',
    position: index,
  }))
const keyAt = (index: number) => `NSE:S${index}`
/** The keys the socket follows now, without the mode the harness adds. */
const live = () => feed.live().map((key) => key.replace(/:LTP$/, ''))

function mount(
  items: WatchlistItem[],
  follow: Parameters<typeof useLiveRows>[0]['follow'] | undefined,
  marketOpen = true
) {
  return renderHook(
    (props: { items: WatchlistItem[]; follow: typeof follow }) =>
      useLiveRows({
        items: props.items,
        apiKey: 'k',
        marketOpen: () => marketOpen,
        follow: props.follow,
      }),
    { initialProps: { items, follow } }
  )
}

const settle = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
    frames.flush()
  })
}

/** Rows laid out 56 px apart in a 600 px screen, and the scroll position as the index of the first row. */
const screen = { first: 0 }
function layOut(count: number) {
  document.body.innerHTML = ''
  for (let index = 0; index < count; index += 1) {
    const element = document.createElement('li')
    element.dataset.rowIndex = String(index)
    element.getBoundingClientRect = () => {
      const top = (index - screen.first) * 56
      return { top, bottom: top + 56 } as DOMRect
    }
    document.body.append(element)
  }
}
const scrollTo = async (first: number) => {
  screen.first = first
  window.dispatchEvent(new Event('scroll'))
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300)
    frames.flush()
  })
}

beforeEach(() => {
  feed.reset()
  frames.install()
  fakeClock()
  vi.setSystemTime(T0)
  read.calls = []
  screen.first = 0
  vi.stubGlobal('innerHeight', 600)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('the cap of 250', () => {
  it('follows the sheet, then the underlyings, then the rows in order, and stops at 250', async () => {
    const underlyings = Array.from({ length: 15 }, (_, index) => `NSE_INDEX:U${index}`)
    const { result } = mount(itemsOf(240), { sheet: [keyAt(239)], underlyings })
    await settle()
    expect(live()).toHaveLength(FOLLOW_CAP)
    expect(live()).toContain(keyAt(239)) // the sheet's row, last in the list, comes first
    expect(live()).toContain('NSE_INDEX:U14')
    // 16 are in before any row, so rows 0 to 233 follow and 234 to 238 do not.
    expect(result.current.rows[233].outside).toBeUndefined()
    expect(result.current.rows[234].outside).toBe('cap')
    expect(result.current.rows[238].outside).toBe('cap')
    expect(result.current.rows[239].outside).toBeUndefined()
  })

  it('follows the whole of a list that fits, with the switch off or on', async () => {
    const off = mount(itemsOf(250), undefined)
    await settle()
    expect(live()).toHaveLength(250)
    expect(off.result.current.rows.every((row) => row.outside === undefined)).toBe(true)
    off.unmount()
    feed.reset()
    const on = mount(itemsOf(250), { sheet: [] })
    await settle()
    expect(live()).toHaveLength(250)
    expect(on.result.current.rows.every((row) => row.outside === undefined)).toBe(true)
  })

  it('shows a row past the cap with the last read, and never reads it on a timer', async () => {
    const underlyings = Array.from({ length: 15 }, (_, index) => `NSE_INDEX:U${index}`)
    const { result } = mount(itemsOf(240), { underlyings })
    await settle()
    expect(read.calls).toHaveLength(1) // the one read of the whole list at open
    const openedAt = result.current.rows[238].view.at
    expect(result.current.rows[238].view.ltp).not.toBeNull()
    read.calls = []
    for (let minute = 0; minute < 30; minute += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000)
        frames.flush()
      })
    }
    // Thirty minutes of silence: the followed rows are re-read by the quiet re-sync, the others never.
    expect(read.calls.length).toBeGreaterThan(0)
    const outside = new Set(Array.from({ length: 5 }, (_, index) => keyAt(235 + index)))
    for (const call of read.calls)
      for (const key of call)
        expect([key, call.length, outside.has(key)]).toEqual([key, call.length, false])
    expect(result.current.rows[238].outside).toBe('cap')
    expect(result.current.rows[238].view.at).toBe(openedAt)
  })

  it('re-reads a followed quiet row once a minute, as it always did', async () => {
    mount(itemsOf(3), { sheet: [] })
    await settle()
    read.calls = []
    await act(async () => {
      await vi.advanceTimersByTimeAsync(125_000)
      frames.flush()
    })
    expect(read.calls.length).toBeGreaterThan(0)
    expect(new Set(read.calls.flat())).toEqual(new Set([keyAt(0), keyAt(1), keyAt(2)]))
  })
})

describe('the window of a list longer than 250', () => {
  const items = itemsOf(400)

  it('follows the first 120 rows at open', async () => {
    const { result } = mount(items, { sheet: [], listKey: 'built-in' })
    layOut(400)
    await settle()
    expect(live()).toHaveLength(WINDOW)
    expect(result.current.rows[119].outside).toBeUndefined()
    expect(result.current.rows[120].outside).toBe('window')
  })

  it('is centred on the first visible row, 300 ms after the scrolling stops', async () => {
    const { result } = mount(items, { sheet: [], listKey: 'built-in' })
    layOut(400)
    await settle()
    screen.first = 200
    window.dispatchEvent(new Event('scroll'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(299)
      frames.flush()
    })
    expect(live()).toContain(keyAt(0)) // not yet: the scrolling may not have stopped
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
      frames.flush()
    })
    expect(live()).toHaveLength(WINDOW)
    expect(live()).toContain(keyAt(140))
    expect(live()).toContain(keyAt(259))
    expect(live()).not.toContain(keyAt(139))
    expect(live()).not.toContain(keyAt(260))
    expect(result.current.rows[200].outside).toBeUndefined()
    expect(result.current.rows[0].outside).toBe('window')
  })

  it('keeps scrolling a held scroll from moving it: the 300 ms starts again with each scroll', async () => {
    mount(items, { sheet: [], listKey: 'built-in' })
    layOut(400)
    await settle()
    for (let step = 1; step <= 10; step += 1) {
      screen.first = 20 * step
      window.dispatchEvent(new Event('scroll'))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
        frames.flush()
      })
    }
    expect(live()).toContain(keyAt(0)) // never settled
  })

  it('moves only when the visible rows leave the middle half, and then swaps about 30 keys', async () => {
    mount(items, { sheet: [], listKey: 'built-in' })
    layOut(400)
    await settle()
    await scrollTo(200) // window 140..259
    const before = new Set(live())
    const opened = feed.opened.length
    await scrollTo(220) // visible 220..230: the last row is outside 170..229, so it moves
    // (first 220 is inside, last 230 is not)
    expect(live()).toContain(keyAt(160))
    const mid = new Set(live())
    await scrollTo(200) // visible 200..210 sits in the middle half of 160..279 (190..249)
    expect(new Set(live())).toEqual(mid)
    const joined = [...mid].filter((key) => !before.has(key)).length
    expect(joined).toBe(20)
    expect(feed.opened.length - opened).toBe(20)
  })

  it('swaps 31 keys when the visible rows step one past the edge', async () => {
    mount(items, { sheet: [], listKey: 'built-in' })
    layOut(400)
    await settle()
    await scrollTo(200) // window 140..259, middle half 170..229
    const before = new Set(live())
    await scrollTo(169) // first visible 169 is outside
    const after = new Set(live())
    expect([...after].filter((key) => !before.has(key))).toHaveLength(31)
    expect([...before].filter((key) => !after.has(key))).toHaveLength(31)
  })
})

describe('a row that leaves the window keeps its last tick', () => {
  it('shows the tick and the time of it, never the read made when the list opened', async () => {
    const { result } = mount(itemsOf(400), { sheet: [], listKey: 'built-in' })
    layOut(400)
    await settle()
    const opened = result.current.rows[5].view
    expect(opened.ltp).toBe(105) // the opening read: ltp 100 + its place in the call
    const tickAt = T0 + 5 * 60_000
    act(() => {
      feed.push('S5', 'NSE', { ltp: 123.45, prev_close: 100 }, 'websocket', tickAt)
      frames.flush()
    })
    expect(result.current.rows[5].view.ltp).toBe(123.45)
    await scrollTo(300)
    const left = result.current.rows[5]
    expect(left.outside).toBe('window')
    expect(left.view.ltp).toBe(123.45)
    expect(left.view.at).toBe(tickAt)
    expect(left.view.changePercent).toBeCloseTo(23.45)
    expect(left.direction).toBeNull()
    expect(left.tickAt).toBeNull()
    expect(live()).not.toContain(keyAt(5))
  })

  it('shows the opening read for a row that never ticked', async () => {
    const { result } = mount(itemsOf(400), { sheet: [], listKey: 'built-in' })
    layOut(400)
    await settle()
    await scrollTo(300)
    expect(result.current.rows[7].outside).toBe('window')
    expect(result.current.rows[7].view.ltp).toBe(107)
  })

  it('goes back to the live figure when the window returns to it', async () => {
    const { result } = mount(itemsOf(400), { sheet: [], listKey: 'built-in' })
    layOut(400)
    await settle()
    act(() => {
      feed.push('S5', 'NSE', { ltp: 123.45, prev_close: 100 }, 'websocket', T0 + 1000)
      frames.flush()
    })
    await scrollTo(300)
    await scrollTo(0)
    act(() => {
      feed.push('S5', 'NSE', { ltp: 130, prev_close: 100 }, 'websocket', T0 + 2000)
      frames.flush()
    })
    expect(result.current.rows[5].outside).toBeUndefined()
    expect(result.current.rows[5].view.ltp).toBe(130)
  })

  it('keeps at most one entry per row of the open list', async () => {
    const { result, rerender } = mount(itemsOf(400), { sheet: [], listKey: 'built-in' })
    layOut(400)
    await settle()
    act(() => {
      for (let index = 0; index < 120; index += 1)
        feed.push(`S${index}`, 'NSE', { ltp: 200 + index, prev_close: 100 }, 'websocket', T0 + 1)
      frames.flush()
    })
    await scrollTo(300)
    const other = itemsOf(10).map((item) => ({ ...item, symbol: `T${item.position}` }))
    rerender({ items: other, follow: { sheet: [], listKey: 'other' } })
    await settle()
    // The rows of the old list are gone with it: a new list starts from its own reads.
    expect(result.current.rows).toHaveLength(10)
    expect(result.current.rows[3].view.ltp).toBe(103)
  })
})

describe('the snapshot', () => {
  it('reads the whole list at open, one request per 500 keys', async () => {
    mount(itemsOf(1100), { sheet: [], listKey: 'built-in' })
    layOut(1100)
    await settle()
    expect(read.calls.map((call) => call.length).sort((a, b) => b - a)).toEqual([500, 500, 100])
    expect(new Set(read.calls.flat()).size).toBe(1100)
    expect(live()).toHaveLength(WINDOW) // the socket follows the window, not the list
  })

  it('reads a list of 500 keys or fewer in one request, as before', async () => {
    mount(itemsOf(250), { sheet: [] })
    await settle()
    expect(read.calls).toHaveLength(1)
    expect(read.calls[0]).toHaveLength(250)
  })

  it('reads the whole list again on a reconnect, and never on a timer of its own', async () => {
    mount(itemsOf(400), { sheet: [], listKey: 'built-in' })
    layOut(400)
    await settle()
    expect(read.calls).toHaveLength(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000) // the opening read is no longer fresh
    })
    act(() => feed.setState({ connectionEpoch: 2 }))
    await settle()
    expect(read.calls).toHaveLength(2)
    expect(read.calls[1]).toHaveLength(400)
  })
})
