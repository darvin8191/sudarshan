/**
 * Size by risk (B4: M5.11): whole lots between the price and the stop for no more than the sum
 * at risk, the price each order type is measured from, and the sum kept per segment.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { entryOf, RISK_KEY, readRisk, rememberRisk, sizeByRisk } from './sizeHelper'

describe('the size a risk allows', () => {
  const nifty = { side: 'BUY' as const, entry: 96.5, lotSize: 65 }

  it('is whole lots between the price and the stop, never rounded up', () => {
    // 15.00 to the stop is 975 a lot.
    expect(sizeByRisk({ ...nifty, risk: 1950, stop: 81.5 })).toEqual({
      kind: 'size',
      lots: 2,
      units: 130,
      atRisk: 1950,
      perUnit: 15,
    })
    expect(sizeByRisk({ ...nifty, risk: 1949.99, stop: 81.5 })).toMatchObject({ lots: 1 })
    expect(sizeByRisk({ ...nifty, risk: 2924, stop: 81.5 })).toMatchObject({ lots: 2 })
  })

  it('counts a quotient a hair under a whole number as that number (0.3 / 0.1)', () => {
    // 0.3 / (0.2 - 0.1) is 2.9999999999999996 in floating point.
    expect(sizeByRisk({ side: 'BUY', entry: 0.2, stop: 0.1, lotSize: 1, risk: 0.3 })).toMatchObject(
      { lots: 3 }
    )
  })

  it('puts a sell’s stop above the price, and says so when it is not', () => {
    expect(sizeByRisk({ ...nifty, side: 'SELL', risk: 1950, stop: 111.5 })).toMatchObject({
      lots: 2,
      units: 130,
    })
    expect(sizeByRisk({ ...nifty, side: 'SELL', risk: 1950, stop: 90 })).toEqual({
      kind: 'why',
      text: 'For a sell, the stop goes above the price.',
    })
    expect(sizeByRisk({ ...nifty, risk: 1950, stop: 96.5 })).toEqual({
      kind: 'why',
      text: 'For a buy, the stop goes below the price.',
    })
  })

  it('says when one lot, or one share, already risks more than the sum', () => {
    expect(sizeByRisk({ ...nifty, risk: 500, stop: 81.5 })).toEqual({
      kind: 'why',
      text: 'One lot risks ₹975.00 at this stop, more than ₹500.00.',
    })
    expect(sizeByRisk({ side: 'BUY', entry: 1452.3, stop: 1400, lotSize: 1, risk: 40 })).toEqual({
      kind: 'why',
      text: 'One share risks ₹52.30 at this stop, more than ₹40.00.',
    })
  })

  it('counts shares in cash, where a lot is one', () => {
    expect(
      sizeByRisk({ side: 'BUY', entry: 1452.3, stop: 1400.3, lotSize: 1, risk: 1000 })
    ).toMatchObject({ lots: 19, units: 19 })
  })

  it('says nothing until a sum and a stop are typed, and waits for a price', () => {
    expect(sizeByRisk({ ...nifty, risk: null, stop: 81.5 })).toBeNull()
    expect(sizeByRisk({ ...nifty, risk: 1950, stop: null })).toBeNull()
    expect(sizeByRisk({ ...nifty, risk: 0, stop: 81.5 })).toBeNull()
    expect(sizeByRisk({ ...nifty, entry: null, risk: 1950, stop: 81.5 })).toEqual({
      kind: 'why',
      text: 'Waiting for the price to size from.',
    })
  })
})

describe('the price a size is measured from', () => {
  it('is a stop order’s trigger, a limit order’s limit and a market order’s last price', () => {
    const typed = { price: 95, trigger: 96, ltp: 96.5 }
    expect(entryOf({ ...typed, priceType: 'SL' })).toBe(96)
    expect(entryOf({ ...typed, priceType: 'SL-M' })).toBe(96)
    expect(entryOf({ ...typed, priceType: 'LIMIT' })).toBe(95)
    expect(entryOf({ ...typed, priceType: 'MARKET' })).toBe(96.5)
    expect(entryOf({ ...typed, priceType: 'MARKET', ltp: undefined })).toBeNull()
    expect(entryOf({ ...typed, priceType: 'LIMIT', price: 0 })).toBeNull()
  })
})

describe('the sum kept on this phone', () => {
  beforeEach(() => localStorage.clear())

  it('is kept per segment, and only a sum', () => {
    rememberRisk('NFO', 2000)
    rememberRisk('NSE', 1500)
    rememberRisk('NSE', -5)
    expect(readRisk('BFO')).toBe(2000)
    expect(readRisk('BSE')).toBe(1500)
    expect(readRisk('MCX')).toBeNull()
    expect(readRisk('NSE_INDEX')).toBeNull()
    expect(JSON.parse(localStorage.getItem(RISK_KEY) ?? '{}')).toEqual({ fno: 2000, cash: 1500 })
  })
})
