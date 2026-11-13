/** The filter row in the address, and the request it makes. */
import { describe, expect, it } from 'vitest'
import { dayToField, fieldToDay, filterOf, narrowedBy, requestOf, withFilter } from './filters'

const params = (s: string) => new URLSearchParams(s)

describe('the filter in the address', () => {
  it('starts at the month with nothing narrowed, and ignores what it does not know', () => {
    expect(filterOf(params(''))).toEqual({
      period: 'month',
      from: null,
      to: null,
      strategy: '',
      playbook: null,
      tag: null,
      expiry: '',
    })
    expect(
      filterOf(params('period=decade&expiry=sometimes&playbook=0&tag=x&from=2026')).period
    ).toBe('month')
    expect(filterOf(params('expiry=sometimes')).expiry).toBe('')
    expect(filterOf(params('playbook=0&tag=x')).playbook).toBeNull()
    expect(filterOf(params('period=custom&from=20261301&to=20261113')).from).toBeNull()
  })

  it('round trips, leaves defaults out and keeps what is not the filter’s', () => {
    const filter = filterOf(
      params(
        'view=analytics&mode=sandbox&period=custom&from=20261101&to=20261113&strategy=ORB&playbook=4&tag=2&expiry=expiry'
      )
    )
    expect(filter).toMatchObject({
      period: 'custom',
      from: 20261101,
      to: 20261113,
      strategy: 'ORB',
      playbook: 4,
      tag: 2,
      expiry: 'expiry',
    })
    const out = withFilter(params('view=analytics&mode=sandbox'), filter).toString()
    expect(filterOf(params(out))).toEqual(filter)
    expect(out).toContain('view=analytics')
    expect(out).toContain('mode=sandbox')
    expect(withFilter(params('period=week&strategy=ORB'), filterOf(params(''))).toString()).toBe('')
    expect(withFilter(params(''), { ...filter, period: 'week' }).toString()).not.toContain('from=')
  })

  it('counts the filters beyond the period', () => {
    expect(narrowedBy(filterOf(params('period=all')))).toBe(0)
    expect(narrowedBy(filterOf(params('strategy=ORB&tag=2')))).toBe(2)
  })
})

describe('the request', () => {
  it('names a period, carries the mode, and sends only the filters that are on', () => {
    expect(requestOf('live', filterOf(params('period=fy')))).toEqual({ mode: 'live', period: 'fy' })
    expect(
      requestOf('sandbox', filterOf(params('strategy=ORB&playbook=4&tag=2&expiry=non_expiry')))
    ).toEqual({
      mode: 'sandbox',
      period: 'month',
      strategy: 'ORB',
      playbook: 4,
      tag: 2,
      expiry: 'non_expiry',
    })
  })

  it('sends a custom period as its two days, earlier first, and waits for both', () => {
    expect(requestOf('live', filterOf(params('period=custom&from=20261113&to=20261101')))).toEqual({
      mode: 'live',
      from: 20261101,
      to: 20261113,
    })
    expect(requestOf('live', filterOf(params('period=custom&from=20261113')))).toBeNull()
  })

  it('reads a date field and writes one', () => {
    expect(dayToField(20261113)).toBe('2026-11-13')
    expect(dayToField(null)).toBe('')
    expect(fieldToDay('2026-11-13')).toBe(20261113)
    expect(fieldToDay('')).toBeNull()
  })
})
