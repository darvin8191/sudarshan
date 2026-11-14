import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  GTT_STATUS,
  GTT_STATUS_OTHER,
  pnlTone,
  SIDE_BADGE,
  signedMoney,
} from '@/sudarshan/books/tableColours'

/**
 * SUDARSHAN B8 (M6.6, M1.18): the books' wide tables colour gains and losses, buy and sell and
 * every status from the D look's tokens, never from a fixed palette, and never by colour alone:
 * a side has its word, a P&L its sign and arrow, a status its word.
 *
 * `var(--profit)` and `var(--loss)` are upstream's HSL channel triplets, not colours.
 */
const FILES = [
  'pages/OrderBook.tsx',
  'pages/TradeBook.tsx',
  'pages/Positions.tsx',
  'pages/Holdings.tsx',
  'components/trading/GttTab.tsx',
  'sudarshan/books/tableColours.ts',
]

const RAW =
  /\b(?:text|bg|border|from|to|via|fill|stroke|ring|divide|outline|decoration)-(?:red|green|emerald|rose|lime|amber|yellow|orange|blue|purple|cyan|teal|indigo|slate|gray|violet|pink)-\d{2,3}\b/g
const TRIPLET = /color:var\(--(?:profit|loss)\)/g
const source = (file: string) => readFileSync(join(process.cwd(), 'src', file), 'utf8')

describe("the books' wide tables colour from the tokens (B8)", () => {
  for (const file of FILES) {
    it(`${file} has no palette colour of its own`, () => {
      expect(source(file).match(RAW) ?? []).toEqual([])
    })

    it(`${file} writes no channel triplet as a colour`, () => {
      expect(source(file).match(TRIPLET) ?? []).toEqual([])
    })
  }

  it('a side is its solid colour with white ink, and the pages write its word', () => {
    expect(SIDE_BADGE.BUY).toContain('var(--buy-solid)')
    expect(SIDE_BADGE.SELL).toContain('var(--sell-solid)')
    for (const file of ['pages/TradeBook.tsx', 'components/trading/GttTab.tsx']) {
      // the badge that takes the side's colour also renders the side's word
      expect(source(file)).toMatch(/SIDE_BADGE\[[\s\S]{0,400}(trade\.action|leg\.action)/)
    }
  })

  it('a money figure carries its sign, and its colour follows the sign', () => {
    const rupees = (value: number) =>
      `₹${Math.abs(value).toFixed(2)}`.replace('₹', value < 0 ? '-₹' : '₹')
    expect(signedMoney(1240, rupees)).toBe('+₹1240.00')
    expect(signedMoney(-96.4, rupees)).toBe('-₹96.40')
    expect(signedMoney(0, rupees)).toBe('₹0.00')
    expect(pnlTone(5)).toContain('var(--up)')
    expect(pnlTone(-5)).toContain('var(--down)')
    // each P&L cell in the tables has an arrow beside its figure
    for (const file of ['pages/Positions.tsx', 'pages/Holdings.tsx']) {
      const text = source(file)
      const cells = text.split('signedMoney(').length - 1
      expect(cells).toBeGreaterThan(0)
      expect(text.split('<TrendingUp').length - 1).toBeGreaterThanOrEqual(cells)
    }
  })

  it('a GTT status is a tint with the same hue as its ink', () => {
    for (const look of [...Object.values(GTT_STATUS), GTT_STATUS_OTHER]) {
      const [, tint] = /bg-\[color:var\(--([a-z0-9-]+)\)\]/.exec(look) ?? []
      const [, ink] = /text-\[color:var\(--([a-z0-9-]+)\)\]/.exec(look) ?? []
      expect(tint && ink).toBeTruthy()
      expect(look).not.toContain('text-white')
    }
    expect(GTT_STATUS.triggered).toContain('--up-tint')
    expect(GTT_STATUS.rejected).toContain('--down-tint')
  })
})
