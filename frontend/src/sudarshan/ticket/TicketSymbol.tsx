import { Fragment } from 'react'
import { symbolBreaks } from '@/sudarshan/watchlist/rows'

/**
 * M20.2 (v0.17.0 phone review L5): the order ticket's symbol, which may break only between its
 * parts ("BANKNIFTY30DEC26" over "58500PE"), never inside one: it read "BANKNIFTY30DEC2658500P"
 * over "E" at 320 px with Larger text. A part wider than the whole line still breaks rather than
 * being cut (`break-words`). Copied or read aloud, it is the symbol as written.
 */
export function TicketSymbol({ symbol, exchange }: { symbol: string; exchange: string }) {
  return (
    <>
      {symbolBreaks(symbol, exchange).map((part, index) => (
        <Fragment key={`${index}:${part}`}>
          {index > 0 && <wbr />}
          {part}
        </Fragment>
      ))}
    </>
  )
}
