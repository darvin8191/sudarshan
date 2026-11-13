import { useEffect, useState } from 'react'
import { searchSymbols } from './api'
import { type OtherExpiry, otherExpiries } from './strikeSheet'

export interface SameStrikeArgs {
  underlying: string
  /** The exchange the options trade on (NFO or BFO). */
  exchange: string
  strike: number
  /** The row's own expiry, ISO: only later ones are offered. */
  after: string
}

export interface SameStrike {
  state: 'loading' | 'error' | 'ready'
  rows: OtherExpiry[]
  retry: () => void
}

const NONE: OtherExpiry[] = []

/**
 * The contracts of the same strike on the next expiries, read once when the strike sheet opens (F12). The
 * server's `strikes` op is not on this branch, so they come from the instrument search the page already
 * has (`searchSymbols`, the master contract). Nothing here ticks: the prices come from the list's socket,
 * which follows these contracts only while the sheet is open. `null` reads nothing.
 */
export function useSameStrike(args: SameStrikeArgs | null, apiKey: string | null): SameStrike {
  const key = args ? `${args.exchange}:${args.underlying}:${args.strike}:${args.after}` : null
  const [read, setRead] = useState<{
    key: string
    state: 'error' | 'ready'
    rows: OtherExpiry[]
  } | null>(null)
  const [again, setAgain] = useState(0)
  // biome-ignore lint/correctness/useExhaustiveDependencies: key is the identity of args; again re-reads
  useEffect(() => {
    if (!args || key === null || apiKey === null) return
    let live = true
    searchSymbols(apiKey, `${args.underlying} ${args.strike}`).then(
      (found) => live && setRead({ key, state: 'ready', rows: otherExpiries(found, args) }),
      () => live && setRead({ key, state: 'error', rows: NONE })
    )
    return () => {
      live = false
    }
  }, [key, apiKey, again])
  const mine = read?.key === key ? read : null
  return {
    state: mine?.state ?? 'loading',
    rows: mine?.rows ?? NONE,
    retry: () => {
      setRead(null)
      setAgain((count) => count + 1)
    },
  }
}
