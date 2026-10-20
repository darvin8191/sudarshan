/**
 * The product a ticket opens with, per device and per segment (M8.2).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  PRODUCT_MEANING,
  productsFor,
  readProduct,
  rememberProduct,
  STORAGE_KEY,
  segmentOf,
  ticketProduct,
} from './productPref'

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('segments', () => {
  it('groups exchanges the way their products work', () => {
    expect(segmentOf('NFO')).toBe('fno')
    expect(segmentOf('bfo')).toBe('fno')
    expect(segmentOf('NSE')).toBe('cash')
    expect(segmentOf('BSE')).toBe('cash')
    expect(segmentOf('MCX')).toBe('mcx')
    expect(segmentOf('CDS')).toBe('cds')
    expect(segmentOf('BCD')).toBe('cds')
    expect(segmentOf('NSE_INDEX')).toBeNull()
  })

  it("offers the ticket's own products, its default first", () => {
    expect(productsFor('NFO')).toEqual(['NRML', 'MIS'])
    expect(productsFor('NSE')).toEqual(['CNC', 'MIS'])
    expect(productsFor('MCX')).toEqual(['NRML', 'MIS'])
    expect(productsFor('CDS')).toEqual(['NRML', 'MIS'])
    expect(productsFor('NSE_INDEX')).toEqual([])
  })

  it('says what each product means', () => {
    expect(Object.keys(PRODUCT_MEANING).sort()).toEqual(['CNC', 'MIS', 'NRML'])
  })
})

describe('the one rule every ticket opens with', () => {
  it('opens derivatives on Intraday and cash on Delivery until a pick', () => {
    expect(ticketProduct('NFO')).toBe('MIS')
    expect(ticketProduct('BFO')).toBe('MIS')
    expect(ticketProduct('MCX')).toBe('MIS')
    expect(ticketProduct('CDS')).toBe('MIS')
    expect(ticketProduct('NSE')).toBe('CNC')
    expect(ticketProduct('BSE')).toBe('CNC')
  })

  it("opens on the segment's pick once there is one, and nothing for an index", () => {
    rememberProduct('NFO', 'NRML')
    rememberProduct('NSE', 'MIS')
    expect(ticketProduct('NFO')).toBe('NRML')
    expect(ticketProduct('NSE')).toBe('MIS')
    expect(ticketProduct('MCX')).toBe('MIS')
    expect(ticketProduct('NSE_INDEX')).toBeUndefined()
  })
})

describe('remembering', () => {
  it("leaves the ticket's default until a product is picked", () => {
    expect(readProduct('NFO')).toBeUndefined()
    expect(readProduct('NSE')).toBeUndefined()
  })

  it('remembers a pick for its segment, and only there', () => {
    rememberProduct('NFO', 'MIS')
    expect(readProduct('NFO')).toBe('MIS')
    expect(readProduct('BFO')).toBe('MIS')
    expect(readProduct('NSE')).toBeUndefined()
    expect(readProduct('MCX')).toBeUndefined()

    rememberProduct('NSE', 'CNC')
    expect(readProduct('BSE')).toBe('CNC')
    expect(readProduct('NFO')).toBe('MIS')
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')).toEqual({
      fno: 'MIS',
      cash: 'CNC',
    })
  })

  it('ignores a product the segment does not take, stored or asked for', () => {
    rememberProduct('NFO', 'CNC')
    expect(readProduct('NFO')).toBeUndefined()
    rememberProduct('NSE_INDEX', 'MIS')
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()

    localStorage.setItem(STORAGE_KEY, JSON.stringify({ fno: 'CNC', cash: 'NRML' }))
    expect(readProduct('NFO')).toBeUndefined()
    expect(readProduct('NSE')).toBeUndefined()
  })

  it('survives storage it cannot read, and the next pick repairs it', () => {
    localStorage.setItem(STORAGE_KEY, '{not json')
    expect(() => readProduct('NFO')).not.toThrow()
    rememberProduct('NFO', 'NRML')
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')).toMatchObject({ fno: 'NRML' })
    expect(readProduct('NFO')).toBe('NRML')
  })

  it('lasts for the visit when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    rememberProduct('MCX', 'MIS')
    expect(readProduct('MCX')).toBe('MIS')
  })
})
