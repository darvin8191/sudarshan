/** F9: what the page follows, with vectors typed by hand (nothing here is computed by the code under test). */
import { describe, expect, it } from 'vitest'
import { FOLLOW_CAP, followPlan, instrumentOf, rowFollowed, WINDOW, windowStart } from './follow'

const rowsOf = (count: number, prefix = 'NSE:R') =>
  Array.from({ length: count }, (_, index) => ({ key: `${prefix}${index}` }))

describe('the count order and the stop at 250', () => {
  it('counts the sheet, then the underlyings once each, then the rows in display order', () => {
    const plan = followPlan({
      sheet: ['NSE:S1', 'NSE:S2'],
      underlyings: ['NSE:U1', 'NSE:U1', 'NSE:U2', 'NSE:S1'],
      rows: rowsOf(250),
    })
    // 2 + 2 distinct underlyings = 4 are in before any row; the rows then fill 246 places.
    expect(plan.keys.size).toBe(250)
    expect([...plan.keys].slice(0, 4)).toEqual(['NSE:S1', 'NSE:S2', 'NSE:U1', 'NSE:U2'])
    expect(plan.keys.has('NSE:R245')).toBe(true)
    expect(plan.keys.has('NSE:R246')).toBe(false)
    expect(plan.window).toBeNull()
  })

  it('counts an instrument once however many ways it is asked for', () => {
    const plan = followPlan({ sheet: ['NSE:R0'], underlyings: ['NSE:R0'], rows: rowsOf(3) })
    expect([...plan.keys]).toEqual(['NSE:R0', 'NSE:R1', 'NSE:R2'])
  })

  it('follows every row of a list that fits', () => {
    const rows = rowsOf(250)
    const plan = followPlan({ rows })
    expect(plan.keys.size).toBe(250)
    expect(rows.every((row) => rowFollowed(row, plan))).toBe(true)
  })

  it('lets the sheet and the underlyings take the places the last rows would have had', () => {
    const rows = rowsOf(240)
    const plan = followPlan({
      underlyings: Array.from({ length: 15 }, (_, index) => `NSE:U${index}`),
      rows,
    })
    expect(plan.keys.size).toBe(250)
    expect(rowFollowed(rows[234], plan)).toBe(true)
    expect(rowFollowed(rows[235], plan)).toBe(false)
    expect(rowFollowed(rows[239], plan)).toBe(false)
  })

  it('stops at 250 even when the sheet alone asks for more', () => {
    const sheet = Array.from({ length: 260 }, (_, index) => `NSE:S${index}`)
    const plan = followPlan({ sheet, rows: rowsOf(5) })
    expect(plan.keys.size).toBe(250)
    expect(plan.keys.has('NSE:R0')).toBe(false)
  })
})

describe('a strike row counts 2', () => {
  const strike = { key: 'NFO:X', keys: ['NFO:X-CE', 'NFO:X-PE'] }

  it('fits when two places are left', () => {
    const rows = [...rowsOf(248), strike]
    const plan = followPlan({ rows })
    expect(plan.keys.size).toBe(250)
    expect(plan.keys.has('NFO:X-CE') && plan.keys.has('NFO:X-PE')).toBe(true)
    expect(rowFollowed(strike, plan)).toBe(true)
  })

  it('stops at it when only one place is left, and takes nothing after it', () => {
    const rows = [...rowsOf(249), strike]
    const plan = followPlan({ rows })
    expect(plan.keys.size).toBe(249)
    expect(rowFollowed(strike, plan)).toBe(false)
    const after = followPlan({ rows: [...rowsOf(248), strike, { key: 'NSE:LAST' }] })
    expect(after.keys.has('NSE:LAST')).toBe(false)
  })
})

describe('the window of a list longer than 250', () => {
  const rows = rowsOf(400)

  it('offers 120 rows from where it starts', () => {
    const plan = followPlan({ rows, start: 100 })
    expect(plan.window).toEqual({ start: 100, end: 220 })
    expect(plan.keys.size).toBe(WINDOW)
    expect(plan.keys.has('NSE:R100') && plan.keys.has('NSE:R219')).toBe(true)
    expect(plan.keys.has('NSE:R99') || plan.keys.has('NSE:R220')).toBe(false)
  })

  it('keeps the window on the list at its far end', () => {
    expect(followPlan({ rows, start: 390 }).window).toEqual({ start: 280, end: 400 })
  })

  it('puts the sheet first, so the window shrinks by what it brings', () => {
    const plan = followPlan({ sheet: ['NSE:ELSEWHERE'], rows, start: 0 })
    expect(plan.keys.size).toBe(121)
    expect(plan.keys.has('NSE:ELSEWHERE')).toBe(true)
  })

  it('is centred on the first visible row', () => {
    expect(windowStart(400, { first: 200, last: 209 }, null)).toBe(140)
    expect(windowStart(400, { first: 0, last: 9 }, null)).toBe(0)
    expect(windowStart(400, { first: 395, last: 399 }, null)).toBe(280)
  })

  it('moves only when the visible rows leave its middle half', () => {
    // The window 140..259 has 170..229 for its middle half.
    expect(windowStart(400, { first: 170, last: 179 }, 140)).toBe(140)
    expect(windowStart(400, { first: 220, last: 229 }, 140)).toBe(140)
    expect(windowStart(400, { first: 169, last: 178 }, 140)).toBe(109)
    expect(windowStart(400, { first: 221, last: 230 }, 140)).toBe(161)
    // A move of one row past the edge swaps about 30 of the 120.
    expect(Math.abs(windowStart(400, { first: 169, last: 178 }, 140) - 140)).toBe(31)
  })

  it('has none for a list that fits', () => {
    expect(windowStart(FOLLOW_CAP, { first: 100, last: 109 }, null)).toBe(0)
  })
})

describe('keys', () => {
  it('turns a cache key back into an instrument', () => {
    expect(instrumentOf('NSE_INDEX:NIFTY 50')).toEqual({ exchange: 'NSE_INDEX', symbol: 'NIFTY 50' })
  })
})
