/**
 * F6, the arithmetic: the change from today's open, and the order of a sort. Vectors are typed
 * by hand from the design (3.4), never produced by the code under test.
 */
import { expect, describe as group, it } from 'vitest'
import type { QuotesData } from '@/api/trading'
import { OPEN_UNKNOWN, rowView, type Snapshot } from './rows'
import {
  directions,
  isDefaultView,
  LONG_LIST,
  needsFullSnapshot,
  pickSort,
  rankGroups,
  type SortEntry,
  sortEntries,
  sortWords,
} from './sort'
import type { Pref } from './v2/types'

const AT = 1_760_000_000_000
const snap = (ltp: number, prev_close: number, open: number): Snapshot => {
  const quote: QuotesData = {
    ltp,
    prev_close,
    open,
    high: 0,
    low: 0,
    volume: 0,
    oi: 0,
    bid: 0,
    ask: 0,
  }
  return { quote, at: AT }
}
const pref = (over: Partial<Pref> = {}): Pref => ({
  sort: 'list',
  descending: false,
  basis: 'close',
  held: false,
  ...over,
})
const entry = (
  id: number,
  title: string,
  ltp: number | null,
  changePercent: number | null,
  exchange = 'NSE'
): SortEntry => ({ id, title, exchange, ltp, changePercent })
const ids = (entries: SortEntry[]) => entries.map((each) => each.id)

group('change from today’s open', () => {
  it('measures from the snapshot’s open: 412 on an open of 400 is +12 and +3%', () => {
    const view = rowView(snap(412, 405, 400), undefined, 'open')
    expect(view.change).toBe(12)
    expect(view.changePercent).toBeCloseTo(3, 10)
    expect(view.openMissing).toBe(false)
    expect(view.note).toBeNull()
  })

  it('leaves the previous close alone: the same row on the close is +7', () => {
    expect(rowView(snap(412, 405, 400), undefined).change).toBe(7)
    expect(rowView(snap(412, 405, 400), undefined, 'close').change).toBe(7)
  })

  it('is null with the words when the open is not known: 0, absent or not a number', () => {
    for (const open of [0, Number.NaN, -1]) {
      const view = rowView(snap(412, 405, open), undefined, 'open')
      expect(view.change).toBeNull()
      expect(view.changePercent).toBeNull()
      expect(view.openMissing).toBe(true)
      expect(view.note).toBe(OPEN_UNKNOWN)
      expect(view.ltp).toBe(412)
    }
    expect(OPEN_UNKNOWN).toBe('Open not known yet')
    expect(rowView(undefined, undefined, 'open').note).toBe(OPEN_UNKNOWN)
  })

  it('never takes the first trade the page saw for the open', () => {
    const tick = {
      symbol: 'A',
      exchange: 'NSE',
      data: { ltp: 410, open: 399 },
      lastUpdate: AT + 5,
      updateSource: 'websocket' as const,
    }
    const view = rowView(snap(0, 405, 0), tick, 'open')
    expect(view.ltp).toBe(410)
    expect(view.change).toBeNull()
    expect(view.openMissing).toBe(true)
  })
})

group('sorting', () => {
  const list = [
    entry(1, 'AAA', 100, 1.5),
    entry(2, 'BBB', 300, -2),
    entry(3, 'CCC', 200, 1.5),
    entry(4, 'DDD', null, null),
    entry(5, 'EEE', 200, 4),
  ]

  it('keeps the list order for list', () => {
    expect(ids(sortEntries(list, pref()))).toEqual([1, 2, 3, 4, 5])
  })

  it('keeps list order among ties, high first and low first', () => {
    expect(ids(sortEntries(list, pref({ sort: 'change', descending: true })))).toEqual([
      5, 1, 3, 2, 4,
    ])
    expect(ids(sortEntries(list, pref({ sort: 'change', descending: false })))).toEqual([
      2, 1, 3, 5, 4,
    ])
    expect(ids(sortEntries(list, pref({ sort: 'price', descending: true })))).toEqual([
      2, 3, 5, 1, 4,
    ])
    expect(ids(sortEntries(list, pref({ sort: 'price', descending: false })))).toEqual([
      1, 3, 5, 2, 4,
    ])
  })

  it('puts a row with no price last, in both directions', () => {
    for (const descending of [true, false]) {
      for (const sort of ['change', 'price'] as const) {
        expect(sortEntries(list, pref({ sort, descending })).at(-1)?.id).toBe(4)
      }
    }
  })

  it('keeps several unpriced rows last in list order', () => {
    const rows = [entry(1, 'A', null, null), entry(2, 'B', 5, 1), entry(3, 'C', null, null)]
    expect(ids(sortEntries(rows, pref({ sort: 'price', descending: true })))).toEqual([2, 1, 3])
    expect(ids(sortEntries(rows, pref({ sort: 'price', descending: false })))).toEqual([2, 1, 3])
  })

  it('sorts names A to Z and Z to A, and exchanges, with ties in list order', () => {
    const rows = [
      entry(1, 'infy', 1, 1, 'NSE'),
      entry(2, 'ABB', 1, 1, 'BSE'),
      entry(3, 'Wipro', 1, 1, 'NSE'),
      entry(4, 'ABB', 1, 1, 'NFO'),
    ]
    expect(ids(sortEntries(rows, pref({ sort: 'name' })))).toEqual([2, 4, 1, 3])
    expect(ids(sortEntries(rows, pref({ sort: 'name', descending: true })))).toEqual([3, 1, 2, 4])
    expect(ids(sortEntries(rows, pref({ sort: 'exchange' })))).toEqual([2, 4, 1, 3])
    expect(ids(sortEntries(rows, pref({ sort: 'exchange', descending: true })))).toEqual([
      1, 3, 4, 2,
    ])
  })

  it('ranks inside each section and never across one', () => {
    const rank = rankGroups(
      [
        [entry(1, 'A', 10, 1), entry(2, 'B', 30, 3)],
        [entry(3, 'C', 20, 2), entry(4, 'D', 40, 4)],
      ],
      pref({ sort: 'price', descending: true })
    )
    // Section one is ranked 2 then 1, section two 4 then 3: the groups keep their places.
    expect([...rank.entries()]).toEqual([
      [2, 0],
      [1, 1],
      [4, 2],
      [3, 3],
    ])
  })

  it('does not change its input', () => {
    const copy = [...list]
    sortEntries(list, pref({ sort: 'price', descending: true }))
    expect(list).toEqual(copy)
  })
})

group('the words and the picks', () => {
  it('says the sort as the line does', () => {
    expect(sortWords(pref({ sort: 'change', descending: true }))).toBe('% change, high first')
    expect(sortWords(pref({ sort: 'price' }))).toBe('price, low first')
    expect(sortWords(pref({ sort: 'name' }))).toBe('name, A to Z')
    expect(sortWords(pref({ sort: 'exchange', descending: true }))).toBe('exchange, Z to A')
  })

  it('lists high first, low first for numbers and A to Z, Z to A for names', () => {
    expect(directions('change').map((each) => each.label)).toEqual(['High first', 'Low first'])
    expect(directions('name').map((each) => each.label)).toEqual(['A to Z', 'Z to A'])
  })

  it('starts a price or change sort high first and a name sort A to Z; keeps a direction within a kind', () => {
    expect(pickSort(pref(), 'change')).toMatchObject({ sort: 'change', descending: true })
    expect(pickSort(pref({ sort: 'change', descending: true }), 'name')).toMatchObject({
      descending: false,
    })
    expect(pickSort(pref({ sort: 'change', descending: false }), 'price')).toMatchObject({
      descending: false,
    })
    expect(pickSort(pref({ sort: 'name', descending: true }), 'list')).toMatchObject({
      descending: false,
    })
  })

  it('is the default only on list order and the previous close', () => {
    expect(isDefaultView(pref())).toBe(true)
    expect(isDefaultView(pref({ sort: 'price' }))).toBe(false)
    expect(isDefaultView(pref({ basis: 'open' }))).toBe(false)
  })

  it('leaves the long-list seam shut up to 250 rows', () => {
    expect(LONG_LIST).toBe(250)
    expect(needsFullSnapshot(250)).toBe(false)
    expect(needsFullSnapshot(251)).toBe(true)
  })
})
