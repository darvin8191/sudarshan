// SUDARSHAN M7.37: the lines a tab's shares make on the Family page, and the end in India's time.
import { describe, expect, it } from 'vitest'
import type { TabShare } from '@/sudarshan/portfolio/types'
import { cardOf, NOW } from './fixtures'
import { fromIstInput, istInputValue, linesFor, ROLES, rowsFor } from './tabShares'

const row = (over: Partial<TabShare>): TabShare => ({
  tab_id: 3,
  viewer: '1',
  level: 'view',
  expires_at: null,
  version: 1,
  ...over,
})
const members = [cardOf(), cardOf({ id: 2, display_name: 'Test Two', role: 'child' })]

describe('every member', () => {
  it('is a share for each role a member can have', () => {
    expect(ROLES).toEqual(['viewer', 'child'])
  })

  it('reads as one line while every role has a share with the same level and end', () => {
    const shares = [row({ viewer: 'role:viewer' }), row({ viewer: 'role:child', version: 4 })]
    const lines = linesFor(shares, 3, members, NOW)
    expect(lines.map((l) => [l.label, l.who, l.rows.length])).toEqual([['Every member', 'all', 2]])
    expect(rowsFor(shares, 3, 'all').map((r) => r.version)).toEqual([1, 4])
  })

  it('shows each role apart when they differ or one is missing, and never hides one', () => {
    const shares = [
      row({ viewer: 'role:viewer' }),
      row({ viewer: 'role:child', level: 'restricted' }),
    ]
    expect(linesFor(shares, 3, members, NOW).map((l) => [l.label, l.who])).toEqual([
      ['Members with the Viewer role', null],
      ['Members with the Child role', null],
    ])
    expect(linesFor([row({ viewer: 'role:child' })], 3, members, NOW)[0].label).toBe(
      'Members with the Child role'
    )
  })
})

describe('one member', () => {
  it('is named, sorted by name, after every member', () => {
    const shares = [
      row({ viewer: '2', level: 'restricted' }),
      row({ viewer: 'role:viewer' }),
      row({ viewer: 'role:child' }),
      row({ viewer: '1' }),
      row({ tab_id: 9, viewer: '1' }),
    ]
    expect(linesFor(shares, 3, members, NOW).map((l) => [l.label, l.who, l.level])).toEqual([
      ['Every member', 'all', 'view'],
      ['Test One', 1, 'view'],
      ['Test Two', 2, 'restricted'],
    ])
  })

  it('a share with a member no longer in the family is shown, to be stopped', () => {
    const [line] = linesFor([row({ viewer: '7' })], 3, members, NOW)
    expect([line.label, line.who]).toEqual(['A member no longer in the family', null])
  })

  it('an ended share says so, and is not dropped', () => {
    const [line] = linesFor([row({ expires_at: NOW - 1 })], 3, members, NOW)
    expect(line.ended).toBe(true)
    expect(linesFor([row({ expires_at: NOW + 1 })], 3, members, NOW)[0].ended).toBe(false)
  })
})

describe('the end of a share, in India time', () => {
  it('reads and writes a date-and-time input as India time, whatever the phone says', () => {
    const at = Date.UTC(2026, 10, 20, 12, 30) // 18:00 in India
    expect(istInputValue(at)).toBe('2026-11-20T18:00')
    expect(fromIstInput('2026-11-20T18:00')).toBe(at)
    expect(fromIstInput('2026-11-20T18:00:00')).toBe(at)
    expect(fromIstInput('')).toBeNull()
    expect(fromIstInput('20/11/2026 18:00')).toBeNull()
  })
})
