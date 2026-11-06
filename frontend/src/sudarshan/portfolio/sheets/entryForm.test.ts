/** The Add sheet's form and what it sends: each kind's fields (design 4.2), in integers. */
import { describe, expect, it } from 'vitest'
import type { Asset } from '../types'
import { buildEntry, chargesOf, emptyForm, type Form, formOf, workedAmount } from './entryForm'
import { entry } from './fixtures'
import { parsePrice, parseQty, parseRupees, scaledText } from './parse'

const TODAY = 20261104
const share = {
  kind: 'listed',
  id: 7,
  name: 'Infosys',
  symbol: 'INFY',
  exchange: 'NSE',
  asset_class: 'equity',
} as Asset
const fund = { kind: 'mf', id: 8, name: 'Fund', asset_class: 'mf_equity' } as Asset
const form = (mode: Form['mode'], patch: Partial<Form> = {}): Form => ({
  ...emptyForm(mode, TODAY, 1),
  asset: { asset: share, priceU: null },
  ...patch,
})

describe('what is typed', () => {
  it('reads rupees, units and prices as integers, and refuses the wrong shape', () => {
    expect(parseRupees('1,234.50')).toBe(123450)
    expect(parseRupees('12.345')).toBeNull() // paise only
    expect(parseQty('10.5')).toBe(10_500_000)
    expect(parseQty('0.0000001')).toBeNull()
    expect(parsePrice('1428.25')).toBe(14_282_500)
    expect(parseRupees('abc')).toBeNull()
    expect(parseRupees('')).toBeNull()
    expect(scaledText(14_282_500, 10_000)).toBe('1428.25')
  })
})

describe('a buy', () => {
  it('works the amount from quantity x price until it is typed', () => {
    const f = form('BUY', { qty: '10', price: '1428.25' })
    expect(workedAmount(f)).toBe('14282.5')
    const built = buildEntry(f, TODAY)
    expect(built.ok && built.entry).toMatchObject({
      kind: 'BUY',
      trade_date: TODAY,
      status: 'posted',
      account_id: 1,
      asset_id: 7,
      qty_u: 10_000_000,
      price_u: 14_282_500,
      amount_paise: 1_428_250,
      charges_paise: 0,
    })
  })

  it('a typed amount wins, and charges break down into parts', () => {
    const f = form('BUY', {
      qty: '10',
      price: '1428.25',
      amount: '14000',
      amountTouched: true,
      useParts: true,
      parts: {
        brokerage: '20',
        stt: '14.30',
        exchange: '',
        sebi: '',
        stamp: '',
        gst: '3.6',
        dp: '',
        other: '',
      },
    })
    expect(chargesOf(f)).toBe(3790)
    const built = buildEntry(f, TODAY)
    expect(built.ok && built.entry.amount_paise).toBe(1_400_000)
    expect(built.ok && built.entry.charges_paise).toBe(3790)
    expect(built.ok && built.entry.charges_detail).toEqual({
      brokerage_paise: 2000,
      stt_paise: 1430,
      gst_paise: 360,
    })
  })

  it('shares are whole numbers; a fund may have decimals', () => {
    const bad = buildEntry(form('BUY', { qty: '10.5', amount: '100', amountTouched: true }), TODAY)
    expect(!bad.ok && bad.words).toBe('Shares are whole numbers.')
    const ok = buildEntry(
      form('BUY', {
        asset: { asset: fund, priceU: null },
        qty: '10.532',
        amount: '100',
        amountTouched: true,
      }),
      TODAY
    )
    expect(ok.ok).toBe(true)
  })

  it('a date after today can only be a draft', () => {
    const built = buildEntry(
      form('BUY', { qty: '1', amount: '1', amountTouched: true, date: '2026-11-05' }),
      TODAY
    )
    expect(built.ok && built.draft).toBe(true)
    expect(built.ok && built.entry.status).toBe('draft')
  })

  it('says what is missing, in words, and sends nothing', () => {
    expect(buildEntry({ ...form('BUY'), asset: null }, TODAY)).toMatchObject({
      ok: false,
      words: 'Choose what this is for.',
    })
    expect(buildEntry(form('BUY', { accountId: null }), TODAY)).toMatchObject({
      ok: false,
      words: 'Choose the account.',
    })
    expect(buildEntry(form('BUY'), TODAY)).toMatchObject({
      ok: false,
      words: 'Enter the quantity.',
    })
  })
})

describe('the other kinds', () => {
  it('a dividend: gross amount and TDS, no quantity', () => {
    const built = buildEntry(form('DIVIDEND', { amount: '500', tds: '50' }), TODAY)
    expect(built.ok && built.entry).toMatchObject({
      kind: 'DIVIDEND',
      amount_paise: 50_000,
      tds_paise: 5000,
      asset_id: 7,
    })
  })

  it('a fee needs no asset', () => {
    const built = buildEntry(form('FEE', { asset: null, amount: '295' }), TODAY)
    expect(built.ok && built.entry).toMatchObject({
      kind: 'FEE',
      amount_paise: 29_500,
      asset_id: null,
    })
  })

  it('a split is a ratio for every account', () => {
    const built = buildEntry(
      form('SPLIT', { accountId: null, ratioNum: '5', ratioDen: '1' }),
      TODAY
    )
    expect(built.ok && built.entry).toMatchObject({
      kind: 'SPLIT',
      account_id: null,
      ratio_num: 5,
      ratio_den: 1,
    })
  })

  it('a bonus is shares received', () => {
    const built = buildEntry(form('BONUS', { qty: '3' }), TODAY)
    expect(built.ok && built.entry).toMatchObject({
      kind: 'BONUS',
      qty_u: 3_000_000,
      account_id: 1,
    })
  })

  it('an opening holding is a transfer from outside with value = cost on the date bought', () => {
    const built = buildEntry(
      form('OPENING', { qty: '10', price: '100', date: '2024-01-10' }),
      TODAY
    )
    expect(built.ok && built.entry).toMatchObject({
      kind: 'TRANSFER',
      account_id: null,
      to_account_id: 1,
      qty_u: 10_000_000,
      amount_paise: 100_000,
      value_paise: 100_000,
      lot_date: 20240110,
    })
  })

  it('a transfer between accounts needs two different ends; from outside needs a value and a cost', () => {
    const same = buildEntry(form('TRANSFER', { qty: '1', accountId: 1, toAccountId: 1 }), TODAY)
    expect(!same.ok && same.words).toBe('Choose two different accounts.')
    const internal = buildEntry(form('TRANSFER', { qty: '1', accountId: 1, toAccountId: 2 }), TODAY)
    expect(internal.ok && internal.entry).toMatchObject({ account_id: 1, to_account_id: 2 })
    const noValue = buildEntry(
      form('TRANSFER', { qty: '1', fromOutside: true, accountId: null, toAccountId: 2 }),
      TODAY
    )
    expect(!noValue.ok && noValue.words).toBe('Enter the value on that day.')
    const outside = buildEntry(
      form('TRANSFER', {
        qty: '2',
        fromOutside: true,
        accountId: null,
        toAccountId: 2,
        value: '500',
        price: '200',
        lotDate: '2023-05-01',
      }),
      TODAY
    )
    expect(outside.ok && outside.entry).toMatchObject({
      account_id: null,
      to_account_id: 2,
      value_paise: 50_000,
      amount_paise: 40_000,
      lot_date: 20230501,
    })
  })
})

describe('Edit and Duplicate', () => {
  it('fill the form from the entry, and build the same entry back', () => {
    const e = entry(5, {
      kind: 'BUY',
      asset_id: 7,
      account_id: 1,
      qty_u: 10_000_000,
      price_u: 14_282_500,
      amount_paise: 1_428_250,
      charges_paise: 2000,
      charges_detail: null,
    })
    const f = formOf(e, [share], TODAY)
    expect(f.mode).toBe('BUY')
    expect(f.qty).toBe('10')
    expect(f.price).toBe('1428.25')
    expect(f.amount).toBe('14282.5')
    const built = buildEntry(f, TODAY)
    expect(built.ok && built.entry).toMatchObject({
      qty_u: 10_000_000,
      amount_paise: 1_428_250,
      charges_paise: 2000,
    })
  })
})

describe('editing what was imported', () => {
  const imported = entry(9, {
    kind: 'TRANSFER',
    account_id: null,
    to_account_id: 1,
    qty_u: 10_000_000,
    price_u: 1_000_000, // 100 x 10 = 1,000.00
    amount_paise: 123_456, // the file said otherwise
    value_paise: 123_456,
    lot_date: 20240101,
    trade_date: 20260101,
  })

  it('keeps the stored amount and value of a transfer from outside while quantity and price are as loaded', () => {
    const f = formOf(imported, [share], TODAY)
    const built = buildEntry({ ...f, note: 'checked' }, TODAY)
    expect(built.ok && built.entry).toMatchObject({ amount_paise: 123_456, value_paise: 123_456 })
  })

  it('works them again from quantity x price once the owner changes either', () => {
    const f = formOf(imported, [share], TODAY)
    const built = buildEntry({ ...f, qty: '11' }, TODAY)
    expect(built.ok && built.entry).toMatchObject({ amount_paise: 110_000 })
  })

  it('keeps the stored value of an opening holding until quantity or price changes', () => {
    const opening = { ...imported, lot_date: imported.trade_date }
    const f = formOf(opening, [share], TODAY)
    expect(f.mode).toBe('OPENING')
    const same = buildEntry(f, TODAY)
    expect(same.ok && same.entry).toMatchObject({ amount_paise: 123_456, value_paise: 123_456 })
    const changed = buildEntry({ ...f, price: '101' }, TODAY)
    expect(changed.ok && changed.entry).toMatchObject({
      amount_paise: 101_000,
      value_paise: 101_000,
    })
  })
})

describe('editing a draft whose date has come', () => {
  it('stays a draft unless it is to be counted, and says it is not a future date', () => {
    const f = form('BUY', { qty: '10', price: '100', accountId: 1 })
    const kept = buildEntry(f, TODAY, true)
    expect(kept.ok && [kept.entry.status, kept.draft, kept.future]).toEqual(['draft', true, false])
    const counted = buildEntry(f, TODAY, false)
    expect(counted.ok && [counted.entry.status, counted.draft]).toEqual(['posted', false])
  })
})
