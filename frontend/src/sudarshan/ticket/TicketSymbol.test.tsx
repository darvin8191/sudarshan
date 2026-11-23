/**
 * M20.2 (v0.17.0 phone review L5): the ticket's symbol breaks only between its parts. At 320 px
 * with Larger text "BANKNIFTY30DEC2658500PE" read "BANKNIFTY30DEC2658500P" over "E". The cases are
 * typed by hand from OpenAlgo's symbol format (base, DDMMMYY, then a strike and CE/PE or FUT).
 */
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { symbolBreaks } from '@/sudarshan/watchlist/rows'
import { TicketSymbol } from './TicketSymbol'

const CASES: Array<[string, string, string[]]> = [
  ['BANKNIFTY30DEC2658500PE', 'NFO', ['BANKNIFTY', '30DEC26', '58500PE']],
  ['NIFTY24NOV2625900CE', 'NFO', ['NIFTY', '24NOV26', '25900CE']],
  ['NIFTY29DEC26FUT', 'NFO', ['NIFTY', '29DEC26', 'FUT']],
  ['NIFTYNXT5025NOV26FUT', 'NFO', ['NIFTYNXT50', '25NOV26', 'FUT']],
  ['BAJAJ-AUTO30DEC2610000CE', 'NFO', ['BAJAJ-AUTO', '30DEC26', '10000CE']],
  ['USDINR26NOV2683.5CE', 'CDS', ['USDINR', '26NOV26', '83.5CE']],
  ['CRUDEOIL18DEC266000PE', 'MCX', ['CRUDEOIL', '18DEC26', '6000PE']],
  ['SENSEX24NOV2680000CE', 'BFO', ['SENSEX', '24NOV26', '80000CE']],
  ['INFY', 'NSE', ['INFY']],
  ['NIFTY 50', 'NSE_INDEX', ['NIFTY 50']],
  ['NIFTY03NOV2622700PE', 'NSE', ['NIFTY03NOV2622700PE']],
]

describe("the ticket's symbol (L5)", () => {
  it.each(CASES)('%s on %s breaks only between its parts', (symbol, exchange, parts) => {
    expect(symbolBreaks(symbol, exchange)).toEqual(parts)
    expect(symbolBreaks(symbol, exchange).join('')).toBe(symbol)
    const { container } = render(
      <span>
        <TicketSymbol symbol={symbol} exchange={exchange} />
      </span>
    )
    expect(container.textContent).toBe(symbol)
    expect(container.querySelectorAll('wbr')).toHaveLength(parts.length - 1)
  })
})
