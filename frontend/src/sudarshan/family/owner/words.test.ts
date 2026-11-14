import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PRIVACY_CATEGORIES } from '@/sudarshan/portfolio/types'
import { CATEGORIES, type Privacy } from '../types'
import { cardOf, NOW } from './fixtures'
import {
  AUDIT_WORDS,
  BOOK_SHARE_RULE,
  CATEGORY_LABEL,
  clockOf,
  devicesLine,
  lastSeenLine,
  PNL_HIDDEN_NOTE,
  privacyLine,
  settled,
  statusOf,
  whenOf,
} from './words'

describe('the privacy categories', () => {
  it('are the categories of the one table the server and the portfolio read', () => {
    // The table is privacy_fields.json (cross-design Y24): if a category is added or dropped there
    // and not here, the owner's page would offer a switch that does nothing, or miss one.
    const table = JSON.parse(
      readFileSync(
        resolve(__dirname, '../../../../../sudarshan/family/privacy_fields.json'),
        'utf8'
      )
    ) as { categories: Record<string, string[]> }
    expect(Object.keys(table.categories).sort()).toEqual([...CATEGORIES].sort())
    expect(Object.keys(CATEGORY_LABEL).sort()).toEqual([...CATEGORIES].sort())
  })

  // The page cannot read the server's files at run time, so its two copies are pinned to them.
  it('says under a hidden P&L the sentence privacy_fields.json gives, word for word', () => {
    const table = JSON.parse(
      readFileSync(
        resolve(__dirname, '../../../../../sudarshan/family/privacy_fields.json'),
        'utf8'
      )
    ) as { pnl_hidden_note: string }
    expect(PNL_HIDDEN_NOTE).toBe(table.pnl_hidden_note)
  })

  it('says about books the sentence the server refuses a book share with, word for word', () => {
    const source = readFileSync(
      resolve(__dirname, '../../../../../sudarshan/family/words.py'),
      'utf8'
    )
    const entry = /"book_share":\s*\(([\s\S]*?)\n\s*\),/.exec(source)?.[1] ?? ''
    const sentence = [...entry.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]).join('')
    expect(sentence.length).toBeGreaterThan(40)
    expect(BOOK_SHARE_RULE).toBe(sentence)
  })

  it('are the categories the portfolio floor is sent with, in the same order (Y6)', () => {
    expect([...CATEGORIES]).toEqual([...PRIVACY_CATEGORIES])
  })

  it('every choice the page sends is one the portfolio keeps: P&L only with quantities', () => {
    for (let bits = 0; bits < 16; bits += 1) {
      const sent = settled({
        pnl: !!(bits & 1),
        qty: !!(bits & 2),
        funds: !!(bits & 4),
        orders: !!(bits & 8),
      })
      expect(!sent.pnl || sent.qty).toBe(true)
    }
  })

  it('read as one line in the order P&L, quantities, funds, orders', () => {
    expect(privacyLine({ pnl: true, qty: true, funds: false, orders: true })).toBe(
      'P&L shown · quantities shown · funds hidden · orders shown'
    )
    expect(privacyLine({ pnl: false, qty: false, funds: false, orders: false })).toBe(
      'P&L hidden · quantities hidden · funds hidden · orders hidden'
    )
  })

  it('send P&L off whenever quantities are off (Y24)', () => {
    const odd: Privacy = { pnl: true, qty: false, funds: true, orders: true }
    expect(settled(odd)).toEqual({ pnl: false, qty: false, funds: true, orders: true })
    const fine: Privacy = { pnl: true, qty: true, funds: false, orders: false }
    expect(settled(fine)).toEqual(fine)
  })
})

describe('the status chip', () => {
  it('says Active for a member who can sign in', () => {
    expect(statusOf(cardOf(), NOW)).toEqual({ kind: 'active', label: 'Active' })
  })
  it('says when an invite ends, in India time, and that it has when it has', () => {
    const invited = cardOf({ status: 'invited', handle: null, invite_expires_at: NOW + 3_000_000 })
    expect(statusOf(invited, NOW).label).toBe('Invited · expires 16:20')
    expect(statusOf(invited, NOW + 3_000_000).label).toBe('Invited · link expired')
    expect(statusOf({ ...invited, invite_expires_at: null }, NOW).label).toBe(
      'Invited · link expired'
    )
  })
  it('says a lock only while it lasts', () => {
    const locked = cardOf({ locked_until: NOW + 600_000 })
    expect(statusOf(locked, NOW)).toEqual({ kind: 'locked', label: 'Locked until 15:40' })
    expect(statusOf(locked, NOW + 600_000).kind).toBe('active')
  })
  it('puts a suspension before a lock', () => {
    expect(statusOf(cardOf({ status: 'suspended', locked_until: NOW + 600_000 }), NOW).label).toBe(
      'Suspended'
    )
  })
})

describe('the small lines', () => {
  it('count devices', () => {
    expect([0, 1, 3].map(devicesLine)).toEqual(['No devices', '1 device', '3 devices'])
  })
  it('say today or the day', () => {
    expect(clockOf(NOW)).toBe('15:30')
    expect(whenOf(NOW - 3_600_000, NOW)).toBe('today 14:30')
    expect(whenOf(NOW - 86_400_000 * 3, NOW)).toBe('11 Nov, 15:30')
    expect(lastSeenLine(null, NOW)).toBe('Not seen yet')
  })
  it('have a sentence for every kind of audit line', () => {
    for (const words of Object.values(AUDIT_WORDS)) expect(words.length).toBeGreaterThan(3)
  })
})
