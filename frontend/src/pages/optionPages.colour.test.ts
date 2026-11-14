import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * SUDARSHAN T3 (M1.18): the option pages colour up and down, calls and puts,
 * buy and sell from the D look's tokens (`--up`, `--down`, `--buy-solid`,
 * `--sell-solid`), never from Tailwind's red and green, so the colour-blind
 * mode and the sunlight contrast reach them too, and each colour has a word,
 * a sign or an arrow beside it.
 *
 * `var(--profit)` and `var(--loss)` are upstream's HSL channel triplets, not
 * colours: written as a colour the browser drops the declaration silently.
 */
const PAGES = [
  'OptionChain',
  'IVChart',
  'CustomStraddle',
  'GEXDashboard',
  'GammaDensity',
  'Arbitrage',
  'IVSmile',
]

const RAW =
  /\b(?:text|bg|border|from|to|via|fill|stroke|ring|divide|outline|decoration)-(?:red|green|emerald|rose|lime)-\d{2,3}\b/g
const TRIPLET = /color:var\(--(?:profit|loss)\)/g

const source = (page: string) =>
  readFileSync(join(process.cwd(), 'src/pages', `${page}.tsx`), 'utf8')

describe('the option pages colour from the tokens (M1.18)', () => {
  for (const page of PAGES) {
    it(`${page} has no red or green of its own`, () => {
      expect(source(page).match(RAW) ?? []).toEqual([])
    })

    it(`${page} writes no channel triplet as a colour`, () => {
      expect(source(page).match(TRIPLET) ?? []).toEqual([])
    })
  }
})
