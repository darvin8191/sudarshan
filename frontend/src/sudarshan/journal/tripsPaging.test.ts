import { describe, expect, it } from 'vitest'
import { closedTrip } from './fixtures'
import { dayBefore, overNewest, type TripsRead, withEarlier } from './tripsPaging'

const day = (id: number, close_day: number, closed_ms: number) =>
  closedTrip({ id, net_paise: 100, close_day, closed_ms })

const held = (over: Partial<TripsRead> = {}): TripsRead => ({
  open: [],
  closed: [day(5, 20261113, 500), day(4, 20261112, 400), day(3, 20261111, 300)],
  more: true,
  partial: [],
  ...over,
})

describe('the closed trades held across reads', () => {
  it('lays a full newest page over the earlier days held, which it keeps as they were', () => {
    const next = overNewest(held(), [], {
      rows: [day(6, 20261113, 600), day(5, 20261113, 500)],
      full: true,
      partial: [],
    })
    expect(next.closed.map((t) => t.id)).toEqual([6, 5, 4, 3])
    expect(next.more).toBe(true)
  })

  it('takes a page that held everything as all there is', () => {
    const next = overNewest(held(), [], { rows: [day(5, 20261113, 500)], full: false, partial: [] })
    expect(next.closed.map((t) => t.id)).toEqual([5])
    expect(next.more).toBe(false)
  })

  it('keeps a partly read day only while it is held', () => {
    const next = overNewest(held({ partial: [20261111, 20261113] }), [], {
      rows: [day(5, 20261113, 500)],
      full: true,
      partial: [],
    })
    expect(next.partial).toEqual([20261111])
  })

  it('adds an earlier page after the oldest day, leaving out what it repeats', () => {
    const next = withEarlier(held(), {
      rows: [day(3, 20261111, 300), day(2, 20261110, 200)],
      full: true,
      partial: [],
    })
    expect(next.closed.map((t) => t.id)).toEqual([5, 4, 3, 2])
    expect(next.more).toBe(true)
    expect(
      withEarlier(held(), { rows: [day(3, 20261111, 300)], full: true, partial: [] }).more
    ).toBe(false)
  })

  it('asks for the days before the oldest one held, across a month’s start too', () => {
    expect(dayBefore(held())).toBe(20261110)
    expect(dayBefore(held({ closed: [day(1, 20261101, 1)] }))).toBe(20261100)
    expect(dayBefore(held({ closed: [] }))).toBeNull()
  })
})
