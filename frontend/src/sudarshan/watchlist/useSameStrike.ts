import { useEffect, useState } from 'react'
import { OTHER_EXPIRIES, type OtherExpiry } from './strikeSheet'
import { callOp, problemOf } from './v2/api'

export interface SameStrikeArgs {
  underlying: string
  /** The underlying's own exchange (NSE_INDEX, NSE, BSE_INDEX or BSE), as the `strikes` op takes it. */
  exchange: string
  strike: number
  /** The row's own expiry, ISO: only later ones are offered. */
  after: string
}

export interface SameStrike {
  state: 'loading' | 'error' | 'ready'
  rows: OtherExpiry[]
  retry: () => void
  /**
   * The read was refused because the watchlists switch is off on this server: asking again cannot
   * help, so a host may leave the section out. Any other failure is one to try again.
   */
  off: boolean
}

const NONE: OtherExpiry[] = []

/**
 * The contracts of the same strike on the next expiries, read once when the strike sheet opens (F12, now
 * from the server's `strikes` op, F13): one answer for the open expiries, then one for each of the next
 * four after the row's, each asking for the strike alone (`n` 0 around a price equal to the strike, so
 * the ladder's middle is that strike). A side the master lacks is null. Nothing here ticks: the prices
 * come from the list's socket, which follows these contracts only while the sheet is open, and the
 * reads stop with it. `null` reads nothing.
 */
export function useSameStrike(args: SameStrikeArgs | null): SameStrike {
  const key = args ? `${args.exchange}:${args.underlying}:${args.strike}:${args.after}` : null
  const [read, setRead] = useState<{
    key: string
    state: 'error' | 'ready'
    rows: OtherExpiry[]
    off?: boolean
  } | null>(null)
  const [again, setAgain] = useState(0)
  // biome-ignore lint/correctness/useExhaustiveDependencies: key is the identity of args; again re-reads
  useEffect(() => {
    if (!args || key === null) return
    const stop = new AbortController()
    const ask = (exp?: string) =>
      callOp(
        'strikes',
        { u: args.underlying, x: args.exchange, p: args.strike, n: 0, ...(exp ? { exp } : {}) },
        stop.signal
      )
    ;(async () => {
      const first = await ask()
      const later = first.expiries.map((e) => e.date).filter((d) => d > args.after)
      const rows = await Promise.all(
        later.slice(0, OTHER_EXPIRIES).map(async (date): Promise<OtherExpiry> => {
          const got = date === first.expiry ? first : await ask(date)
          const at = got.strikes.find((s) => s.strike === args.strike)
          return { expiry: date, ce: at?.ce ?? null, pe: at?.pe ?? null }
        })
      )
      setRead({ key, state: 'ready', rows })
    })().catch((error: unknown) => {
      if (stop.signal.aborted) return
      setRead({ key, state: 'error', rows: NONE, off: problemOf(error).code === 'off' })
    })
    return () => stop.abort()
  }, [key, again])
  const mine = read?.key === key ? read : null
  return {
    state: mine?.state ?? 'loading',
    rows: mine?.rows ?? NONE,
    off: mine?.off === true,
    retry: () => {
      setRead(null)
      setAgain((count) => count + 1)
    },
  }
}
