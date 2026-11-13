import { Briefcase } from 'lucide-react'
import { cn } from '@/lib/utils'
import { keyOf } from '@/sudarshan/dashboard/live'
import type { HeldBook } from './useHeld'
import type { Row } from './v2/types'

/** What one row shows: the quantity held, and the net position, signed. Zero or absent shows nothing. */
export interface HeldFigures {
  holding: number
  position: number
}

/** A cash symbol: the only kind a holding can belong to, and the only kind matched by ISIN. */
const CASH = new Set(['NSE', 'BSE'])

const NUMBER = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 3 })
const MINUS = '−'

const number = (value: unknown): number => {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

/** The ISIN of each symbol of the view, by EXCHANGE:SYMBOL, for the rows that have one. */
export function isinsOf(rows: readonly Row[] | undefined): ReadonlyMap<string, string> {
  const map = new Map<string, string>()
  for (const { contract } of rows ?? []) {
    if (contract.isin) map.set(keyOf(contract.symbol, contract.exchange), contract.isin)
  }
  return map
}

/**
 * The book indexed for matching. A holding is found by ISIN, so a BSE holding shows on the NSE
 * row; one the broker gave without an ISIN (the sandbox) is found by exchange and symbol. A
 * position is found by exchange and symbol alone, summed over its products.
 */
export function indexBook(book: HeldBook) {
  const byIsin = new Map<string, number>()
  const byKey = new Map<string, number>()
  const byKeyNoIsin = new Map<string, number>()
  const net = new Map<string, number>()
  const add = (map: Map<string, number>, key: string, quantity: number) =>
    map.set(key, (map.get(key) ?? 0) + quantity)
  for (const holding of book.holdings) {
    const quantity = number(holding.quantity)
    const key = keyOf(holding.symbol, holding.exchange)
    const { isin } = holding
    add(byKey, key, quantity)
    if (typeof isin === 'string' && isin) add(byIsin, isin.toUpperCase(), quantity)
    else add(byKeyNoIsin, key, quantity)
  }
  for (const position of book.positions) {
    add(net, keyOf(position.symbol, position.exchange), number(position.quantity))
  }
  return { byIsin, byKey, byKeyNoIsin, net }
}

export type BookIndex = ReturnType<typeof indexBook>

/**
 * The figures for one row. A cash symbol (NSE, BSE) takes its holding by ISIN; a contract (any
 * other exchange) has no holding and is never matched by ISIN. Both take their position exactly,
 * by exchange and symbol.
 */
export function figuresFor(
  index: BookIndex,
  exchange: string,
  symbol: string,
  isin: string | null | undefined
): HeldFigures {
  const key = keyOf(symbol, exchange)
  let holding = 0
  if (CASH.has(exchange.toUpperCase())) {
    holding = isin
      ? (index.byIsin.get(isin.toUpperCase()) ?? 0) + (index.byKeyNoIsin.get(key) ?? 0)
      : (index.byKey.get(key) ?? 0)
  }
  return { holding, position: index.net.get(key) ?? 0 }
}

/** "Holding 25". */
export const holdingWords = (quantity: number) => `Holding ${NUMBER.format(quantity)}`

/** "Position +65" or "Position −30": the sign and the word, never a colour. */
export const positionWords = (net: number) =>
  `Position ${net > 0 ? '+' : MINUS}${NUMBER.format(Math.abs(net))}`

/**
 * The holdings line: what is added to a row's second line. In ink, not in the up or down colour;
 * nothing at all when both are zero.
 */
export function HoldingLine({ holding, position }: Partial<HeldFigures>) {
  const held = holding !== undefined && holding !== 0 && Number.isFinite(holding)
  const open = position !== undefined && position !== 0 && Number.isFinite(position)
  if (!held && !open) return null
  return (
    <span
      data-holding-line="true"
      className="flex shrink-0 items-center gap-x-2 text-xs leading-tight font-semibold text-[color:var(--text-primary)]"
    >
      {held && (
        <span className="flex items-center gap-1">
          <Briefcase aria-hidden="true" className="size-3.5" />
          {holdingWords(holding)}
        </span>
      )}
      {open && <span>{positionWords(position)}</span>}
    </span>
  )
}

/**
 * "Show what I hold" for the View sheet's slot: a switch, 48 dp, whose state is its word and its
 * check, not a colour.
 */
export function HeldSwitch({ on, onChange }: { on: boolean; onChange: (next: boolean) => void }) {
  return (
    <fieldset className="m-0 border-0 p-0">
      <legend className="px-3 pt-3 pb-1 text-[0.8125rem] font-bold text-[color:var(--text-secondary)]">
        Holdings
      </legend>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        onClick={() => onChange(!on)}
        className={cn(
          'flex min-h-12 w-full items-center gap-3 rounded-xl px-3 text-left text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring',
          on && 'bg-[color:var(--primary-tint)] font-bold'
        )}
      >
        <Briefcase aria-hidden="true" className="size-5" />
        Show what I hold
        <span aria-hidden="true" className="ml-auto text-sm font-bold">
          {on ? 'On' : 'Off'}
        </span>
      </button>
    </fieldset>
  )
}
