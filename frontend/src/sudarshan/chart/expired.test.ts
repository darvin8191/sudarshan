import { describe, expect, it } from 'vitest'
import { contractOf, dayWords, expiredOn, isDerivative, onExpiry, underlyingOf } from './expired'

/** A moment in India on a day of November 2026. */
const ist = (day: number, h: number, m = 0) => Date.UTC(2026, 10, day, h, m) - 19_800_000

describe('a contract read from its symbol (TD2)', () => {
  it.each([
    [
      'NIFTY17NOV2625000CE',
      'NFO',
      { base: 'NIFTY', expiry: '2026-11-17', strike: '25000', right: 'CE' },
    ],
    [
      'VEDL25APR24292.5PE',
      'NFO',
      { base: 'VEDL', expiry: '2024-04-25', strike: '292.5', right: 'PE' },
    ],
    ['BANKNIFTY24APR24FUT', 'NFO', { base: 'BANKNIFTY', expiry: '2024-04-24' }],
    ['CRUDEOILM20MAY24FUT', 'MCX', { base: 'CRUDEOILM', expiry: '2024-05-20' }],
    [
      'SENSEX20NOV2681000PE',
      'BFO',
      { base: 'SENSEX', expiry: '2026-11-20', strike: '81000', right: 'PE' },
    ],
    ['M&M27NOV263500CE', 'NFO', { base: 'M&M', expiry: '2026-11-27', strike: '3500', right: 'CE' }],
  ])('%s on %s', (symbol, exchange, contract) => {
    expect(contractOf(symbol, exchange)).toEqual(contract)
  })

  it('reads nothing from a share, an index, or a name that is not a contract', () => {
    expect(contractOf('SBIN', 'NSE')).toBeNull()
    expect(contractOf('NIFTY', 'NSE_INDEX')).toBeNull()
    expect(contractOf('NIFTY17NOV2625000CE', 'NSE')).toBeNull()
    expect(contractOf('NIFTY17XYZ2625000CE', 'NFO')).toBeNull()
    expect(isDerivative('nfo')).toBe(true)
    expect(isDerivative('NSE_INDEX')).toBe(false)
  })
})

describe('when a contract has expired', () => {
  it('trades all of its expiry day in India and is gone from the next one', () => {
    expect(expiredOn('NIFTY17NOV2625000CE', 'NFO', ist(17, 15, 29))).toBeNull()
    expect(expiredOn('NIFTY17NOV2625000CE', 'NFO', ist(17, 23, 59))).toBeNull()
    expect(expiredOn('NIFTY17NOV2625000CE', 'NFO', ist(18, 0, 0))).toBe('2026-11-17')
    expect(expiredOn('NIFTY17NOV2625000CE', 'NFO', ist(23, 11, 38))).toBe('2026-11-17')
    // 18:30 UTC on the 17th is midnight in India: the day is India's, not the phone's zone.
    expect(expiredOn('NIFTY17NOV2625000CE', 'NFO', Date.UTC(2026, 10, 17, 18, 29))).toBeNull()
    expect(expiredOn('NIFTY17NOV2625000CE', 'NFO', Date.UTC(2026, 10, 17, 18, 30))).toBe(
      '2026-11-17'
    )
    expect(expiredOn('NIFTY24NOV2625000CE', 'NFO', ist(23, 11, 38))).toBeNull()
    expect(expiredOn('SBIN', 'NSE', ist(23, 11, 38))).toBeNull()
  })

  it('says the day as a trader reads it', () => {
    expect(dayWords('2026-11-17')).toBe('17 Nov 2026')
    expect(dayWords('2027-01-05')).toBe('5 Jan 2027')
  })
})

describe('what trades in its place', () => {
  it('the underlying: an index on its index exchange, a share on its cash exchange', () => {
    const at = (symbol: string, exchange: string) =>
      underlyingOf(contractOf(symbol, exchange) ?? { base: '', expiry: '' }, exchange)
    expect(at('NIFTY17NOV2625000CE', 'NFO')).toEqual({ symbol: 'NIFTY', exchange: 'NSE_INDEX' })
    expect(at('MIDCPNIFTY17NOV2613000PE', 'NFO')).toEqual({
      symbol: 'MIDCPNIFTY',
      exchange: 'NSE_INDEX',
    })
    expect(at('RELIANCE27NOV26FUT', 'NFO')).toEqual({ symbol: 'RELIANCE', exchange: 'NSE' })
    expect(at('SENSEX20NOV2681000PE', 'BFO')).toEqual({ symbol: 'SENSEX', exchange: 'BSE_INDEX' })
    expect(at('TATASTEEL27NOV26150CE', 'BFO')).toEqual({ symbol: 'TATASTEEL', exchange: 'BSE' })
    // A commodity's underlying is a future, found from the master's expiry list.
    expect(at('CRUDEOIL17NOV265000CE', 'MCX')).toBeNull()
  })

  it('the same strike and side, or the future, on an expiry the master lists', () => {
    const option = contractOf('NIFTY17NOV2625000CE', 'NFO')
    const future = contractOf('CRUDEOILM20NOV26FUT', 'MCX')
    const half = contractOf('VEDL25APR24292.5CE', 'NFO')
    if (!option || !future || !half) throw new Error('not read')
    expect(onExpiry(option, '24-NOV-26')).toBe('NIFTY24NOV2625000CE')
    expect(onExpiry(option, '01-dec-2026')).toBe('NIFTY01DEC2625000CE')
    expect(onExpiry(future, '18-DEC-26')).toBe('CRUDEOILM18DEC26FUT')
    expect(onExpiry(half, '30-MAY-24')).toBe('VEDL30MAY24292.5CE')
    expect(onExpiry(option, '2026-11-24')).toBeNull()
    expect(onExpiry(option, '24-ABC-26')).toBeNull()
  })
})
