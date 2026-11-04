/** What a tap on an allocation bar does, and the filters the service is sent. */
import { describe, expect, it } from 'vitest'
import type { Account, AllocationRow } from '../types'
import { NO_FILTERS } from './Filters'
import { row } from './fixtures'
import { argsOf, isActive, type Narrowing, pick, sliced } from './slices'

const ACCOUNTS = [
  { id: 1, member_id: 10, name: 'A' },
  { id: 2, member_id: 10, name: 'B' },
  { id: 3, member_id: 11, name: 'C' },
] as Account[]
const slice = (key: string): AllocationRow => ({
  key,
  label: key,
  value_paise: 1,
  pct: '10.0000',
  holdings: 1,
})
const NONE: Narrowing = { filters: NO_FILTERS, slice: null }

describe('the arguments', () => {
  it('send only what is set', () => {
    expect(argsOf(NO_FILTERS)).toEqual({})
    expect(argsOf({ ...NO_FILTERS, classes: ['gold'], held: 'over', as_of: 20260912 })).toEqual({
      classes: ['gold'],
      held: 'over',
      as_of: 20260912,
    })
  })
})

describe('a tap on a bar', () => {
  it('filters by class and a second tap takes it off', () => {
    const on = pick('class', slice('gold'), NONE, ACCOUNTS)
    expect(on.filters.classes).toEqual(['gold'])
    expect(isActive('class', slice('gold'), on, ACCOUNTS)).toBe(true)
    expect(pick('class', slice('gold'), on, ACCOUNTS).filters.classes).toEqual([])
  })

  it('an account is the number in the key; a member is all their accounts', () => {
    expect(pick('account', slice('2'), NONE, ACCOUNTS).filters.accounts).toEqual([2])
    const member = pick('member', slice('10'), NONE, ACCOUNTS)
    expect(member.filters.accounts).toEqual([1, 2])
    expect(isActive('member', slice('10'), member, ACCOUNTS)).toBe(true)
    expect(isActive('member', slice('11'), member, ACCOUNTS)).toBe(false)
  })

  it('a sector has no filter in the service: the screen narrows the rows', () => {
    const on = pick('sector', slice('IT'), NONE, ACCOUNTS)
    expect(on.slice).toEqual({ by: 'sector', key: 'IT' })
    const a = row(1, 'A')
    a.asset.sector = 'IT'
    const b = row(2, 'B')
    b.asset.sector = 'Banks'
    expect(sliced([a, b], on.slice).map((r) => r.asset.name)).toEqual(['A'])
    expect(sliced([a, b], null)).toHaveLength(2)
  })
})
