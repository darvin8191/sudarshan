import { describe, expect, it } from 'vitest'
import { expiryIso, otherExpiries, straddleOf } from './strikeSheet'

const hit = (expiry: string, right: string, over = {}) => ({
  symbol: `NIFTY${expiry}${right}`,
  exchange: 'NFO',
  name: 'NIFTY',
  instrumenttype: right,
  lotsize: 65,
  tick_size: 0.05,
  expiry,
  strike: 25900,
  ...over,
})
const args = { underlying: 'NIFTY', exchange: 'NFO', strike: 25900, after: '2026-11-24' }

describe('the strike sheet rules', () => {
  it('reads the master spelling of an expiry', () => {
    expect(expiryIso('24-NOV-26')).toBe('2026-11-24')
    expect(expiryIso('2026-12-01')).toBe('2026-12-01')
    expect(expiryIso('soon')).toBeNull()
  })

  it('takes the next four expiries after the row, of this underlying and strike only', () => {
    const found = [
      '24-NOV-26',
      '01-DEC-26',
      '08-DEC-26',
      '15-DEC-26',
      '22-DEC-26',
      '29-DEC-26',
    ].flatMap((day) => [hit(day, 'CE'), hit(day, 'PE')])
    const rows = otherExpiries(
      [
        ...found,
        hit('01-DEC-26', 'CE', { name: 'BANKNIFTY' }),
        hit('01-DEC-26', 'CE', { strike: 26000 }),
      ],
      args
    )
    expect(rows.map((row) => row.expiry)).toEqual([
      '2026-12-01',
      '2026-12-08',
      '2026-12-15',
      '2026-12-22',
    ])
    expect(rows[0].ce?.symbol).toBe('NIFTY01-DEC-26CE')
    expect(rows[0].pe?.lot).toBe(65)
  })

  it('keeps an expiry that lists one side only, with the other null', () => {
    const rows = otherExpiries([hit('01-DEC-26', 'CE')], args)
    expect(rows).toHaveLength(1)
    expect(rows[0].pe).toBeNull()
  })

  it('adds the straddle only when both sides have a price', () => {
    expect(straddleOf(200.3, 140.25)).toBeCloseTo(340.55)
    expect(straddleOf(200, null)).toBeNull()
  })
})
