/**
 * The phone's value, Today and tab sums against the golden vectors the Python core runs
 * (sudarshan/portfolio/tests/vectors/: one copy, read from there), and the words every status has.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { feedLine } from '@/sudarshan/dashboard/live'
import { feedPriceU, formatPct, pct, roundDiv, roundHalfEven, valuePaise } from './money'
import { FRESHNESS_WORDS, feedStatusWords, TODAY_WORDS } from './statusWords'
import { FRESHNESS_STATES, TODAY_STATES } from './types'
import {
  closedToday,
  previousCloseU,
  rowFigures,
  rowToday,
  tabTotals,
  todayMode,
  type ValuationFixed,
  type ValuationRow,
} from './valuation'

const read = (name: string) =>
  JSON.parse(
    readFileSync(
      join(process.cwd(), '..', 'sudarshan', 'portfolio', 'tests', 'vectors', name),
      'utf8'
    )
  )
const VALUATION = read('valuation.json') as Record<string, any[]>
const MONEY = read('money.json') as Record<string, any[]>

describe('money vectors', () => {
  it.each(MONEY.round_div)('round_div $n / $d', ({ n, d, expect: want }) => {
    expect(Number(roundDiv(BigInt(n), BigInt(d)))).toBe(want)
  })
  it.each(MONEY.value)('value $id', ({ qty_u, price_u, expect: want }) => {
    expect(valuePaise(qty_u, price_u)).toBe(want)
  })
  it.each(MONEY.pct)('pct $n / $d', ({ n, d, expect: want }) => {
    expect(pct(n, d)).toBe(want)
  })
  it.each(MONEY.feed_price_u)('feed_price_u $v', ({ v, expect: want }) => {
    expect(feedPriceU(v)).toBe(want)
  })
  it('a product past 2^53 is still exact', () => {
    // 123456789012 x 98765432101 = 12193263112572168874212: a float product would lose its last digits.
    expect(valuePaise(123_456_789_012, 98_765_432_101)).toBe(121932631125722)
  })
  it('halves go to the even neighbour like Python round', () => {
    expect([0.5, 1.5, 2.5, 1012.5, -0.5].map(roundHalfEven)).toEqual([0, 2, 2, 1012, 0])
    expect(feedPriceU(10.125)).toBe(101_200)
    expect(feedPriceU(0.004)).toBeNull()
  })
  it('a percentage never reads -0.0000', () => {
    expect(pct(-1, 10_000_000)).toBe('0.0000')
    expect(formatPct(-5n)).toBe('-0.0005')
  })
})

describe('valuation.json', () => {
  it.each(VALUATION.rows)('row $id: $title', ({ row, expect: want }) => {
    expect(rowFigures(row)).toEqual(want)
  })

  it.each(VALUATION.tabs)('tab $id: $title', ({ rows, fixed, expect: want }) => {
    expect(tabTotals(rows, fixed)).toEqual(want)
  })

  it.each(VALUATION.closed)('closed $id', ({ rows, expect: want }) => {
    expect(closedToday(rows)).toEqual(want)
  })

  it.each(VALUATION.previous_close)('previous close $id', (c) => {
    expect(previousCloseU(c.price_u, c.prev_close_u, c.price_day, c.today)).toBe(c.expect)
  })

  it.each(VALUATION.modes)('mode $asset_kind $basis -> $expect', (c) => {
    expect(todayMode(c.asset_kind, c.basis, c.price_day, c.today)).toBe(c.expect)
  })

  // The service's morning replays (R1-R3): each frame sets prices on the rows, the tab is whole again.
  describe.each(VALUATION.replays)('replay $id: $title', (replay) => {
    it('every frame is the vector', () => {
      const rows: ValuationRow[] = replay.rows.map((r: ValuationRow) => ({
        ...r,
        price: { ...r.price },
      }))
      for (const frame of replay.frames) {
        for (const s of frame.set) {
          const target = rows[s.row]
          if ('price_u' in s) target.price.price_u = s.price_u
          if ('prev_close_u' in s) target.price.prev_close_u = s.prev_close_u
          if ('today_mode' in s) target.today_mode = s.today_mode
        }
        expect(tabTotals(rows, replay.fixed), frame.note).toEqual(frame.expect)
      }
    })
  })
})

describe('a previous close that is not known', () => {
  const line = {
    account_id: 1,
    qty_u: 10_000_000,
    open_qty_u: 10_000_000,
    cost_paise: 100_000,
    in_today_paise: 0,
    out_today_paise: 0,
    over_year_qty_u: 0,
  }
  const fixed: ValuationFixed = {
    realised_paise: 0,
    income_paise: 0,
    fees_paise: 0,
    money_in_paise: 100_000,
    money_out_paise: 0,
    closed_today_paise: 0,
    closed_today_base_paise: 0,
  }
  const row: ValuationRow = {
    today_mode: 'ok',
    price: { price_u: 1_050_000, prev_close_u: null },
    lines: [line],
  }

  it('gives null and "unknown", never 0, for the row and for the tab', () => {
    const today = rowToday(row)
    expect(today).toMatchObject({ state: 'unknown', paise: null, pct: null, unknown: 1 })
    const tab = tabTotals([row], fixed)
    expect(tab.today).toMatchObject({ state: 'unknown', paise: null, pct: null })
    expect(tab.value_paise).toBe(105_000) // the value does not need a previous close
  })

  it('is not needed for units bought today, only for units held at the open', () => {
    const bought = { ...line, open_qty_u: 0, in_today_paise: 100_000 }
    expect(rowToday({ ...row, lines: [bought] })).toMatchObject({ state: 'ok', paise: 5_000 })
  })

  it('a row with no price is counted at its cost and counted as unpriced', () => {
    const none: ValuationRow = { ...row, price: { price_u: null, prev_close_u: 1_000_000 } }
    expect(tabTotals([none], fixed)).toMatchObject({ value_paise: 100_000, unpriced: 1 })
  })
})

describe('every status has a word', () => {
  it('Today: a sentence for each state, none of them a bare colour or empty', () => {
    for (const state of TODAY_STATES) expect(TODAY_WORDS[state].length).toBeGreaterThan(2)
    expect(TODAY_WORDS.unknown).toBe('Today: not known yet')
  })

  it('freshness: "Old" is a word, and so is its opposite', () => {
    for (const state of FRESHNESS_STATES) expect(FRESHNESS_WORDS[state].length).toBeGreaterThan(2)
    expect(FRESHNESS_WORDS.old).toBe('Old')
  })

  it('the feed: Real-time only while ticks arrive, and a word for every other kind', () => {
    const now = 1_000_000
    const lines = [
      feedLine({ now, marketOpen: true, lastTickAt: now - 1000, lastUpdateAt: now - 1000 }),
      feedLine({ now, marketOpen: true, lastTickAt: now - 60_000, lastUpdateAt: now - 60_000 }),
      feedLine({ now, marketOpen: true, lastTickAt: null, lastUpdateAt: null, connected: false }),
      feedLine({ now, marketOpen: true, lastTickAt: null, lastUpdateAt: null }),
      feedLine({ now, marketOpen: true, lastTickAt: null, lastUpdateAt: null, held: false }),
      feedLine({ now, marketOpen: false, lastTickAt: null, lastUpdateAt: null }),
    ]
    expect(lines.map((l) => l.kind)).toEqual([
      'live',
      'delayed',
      'connecting',
      'waiting',
      'idle',
      'closed',
    ])
    const words = lines.map((l) => feedStatusWords(l, now))
    for (const w of words) expect(w.length).toBeGreaterThan(2)
    expect(words.filter((w) => w === 'Real-time')).toHaveLength(1)
    expect(words[0]).toBe('Real-time')
    expect(words[1]).toMatch(/^Stale/)
    expect(words[5]).toBe('Market closed')
  })
})
