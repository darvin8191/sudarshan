/** F7: the matching and the words of the holdings line, as plain functions. */
import { describe, expect, it } from 'vitest'
import type { Holding, Position } from '@/types/trading'
import { figuresFor, holdingWords, indexBook, isinsOf, positionWords } from './HoldingLine'
import type { Row } from './v2/types'

const holding = (symbol: string, exchange: string, quantity: number, isin?: string): Holding =>
  ({ symbol, exchange, quantity, product: 'CNC', pnl: 0, pnlpercent: 0, isin }) as Holding
const position = (symbol: string, exchange: string, quantity: number, product = 'NRML'): Position =>
  ({ symbol, exchange, quantity, product }) as Position

describe('the words', () => {
  it('writes the holding as a number and the position with its sign and the word', () => {
    expect(holdingWords(25)).toBe('Holding 25')
    expect(holdingWords(12500)).toBe('Holding 12,500')
    expect(positionWords(65)).toBe('Position +65')
    expect(positionWords(-30)).toBe('Position −30')
  })
})

describe('matching', () => {
  const index = indexBook({
    holdings: [
      holding('RELIANCE', 'BSE', 10, 'INE002A01018'),
      holding('RELIANCE', 'NSE', 15, 'INE002A01018'),
      holding('SANDBOX', 'NSE', 7),
    ],
    positions: [
      position('RELIANCE', 'NSE', 40, 'MIS'),
      position('RELIANCE', 'NSE', 25, 'NRML'),
      position('NIFTY25NOVFUT', 'NFO', -30),
      position('NIFTY25NOV24000CE', 'NFO', 0),
    ],
  })

  it('shows a BSE holding on the NSE row, by ISIN, added to the NSE one', () => {
    expect(figuresFor(index, 'NSE', 'RELIANCE', 'INE002A01018').holding).toBe(25)
    expect(figuresFor(index, 'BSE', 'RELIANCE', 'INE002A01018').holding).toBe(25)
  })

  it('matches a holding the broker gave without an ISIN by exchange and symbol', () => {
    expect(figuresFor(index, 'NSE', 'SANDBOX', null).holding).toBe(7)
    expect(figuresFor(index, 'NSE', 'SANDBOX', 'INE999Z01011').holding).toBe(7)
    expect(figuresFor(index, 'BSE', 'SANDBOX', null).holding).toBe(0)
  })

  it('without an ISIN on the row, finds a holding on its own exchange only', () => {
    expect(figuresFor(index, 'NSE', 'RELIANCE', null).holding).toBe(15)
  })

  it('takes a position exactly by exchange and symbol, summed over its products', () => {
    expect(figuresFor(index, 'NSE', 'RELIANCE', 'INE002A01018').position).toBe(65)
    expect(figuresFor(index, 'BSE', 'RELIANCE', 'INE002A01018').position).toBe(0)
    expect(figuresFor(index, 'NFO', 'NIFTY25NOVFUT', null).position).toBe(-30)
  })

  it('never matches a contract by ISIN, even when the row carries one', () => {
    const figures = figuresFor(index, 'NFO', 'RELIANCE25NOVFUT', 'INE002A01018')
    expect(figures).toEqual({ holding: 0, position: 0 })
    expect(figuresFor(index, 'NFO', 'RELIANCE', 'INE002A01018').holding).toBe(0)
  })

  it('reads zero as nothing', () => {
    expect(figuresFor(index, 'NFO', 'NIFTY25NOV24000CE', null)).toEqual({
      holding: 0,
      position: 0,
    })
  })
})

describe('the view rows ISINs', () => {
  it('keeps only the symbols that have one, by EXCHANGE:SYMBOL', () => {
    const row = (symbol: string, exchange: string, isin: string | null) =>
      ({ contract: { symbol, exchange, isin } }) as unknown as Row
    const map = isinsOf([row('abc', 'nse', 'INE1'), row('XYZ', 'NFO', null)])
    expect([...map]).toEqual([['NSE:ABC', 'INE1']])
  })
})
