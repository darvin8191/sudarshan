import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import type { SymbolData } from '@/lib/MarketDataManager'
import { priceOf } from '@/sudarshan/dashboard/live'
import type { FeedState } from '@/sudarshan/live/useFrameLines'
import { FollowResolver, type RowState, type Tick, underlyingKey } from './followRows'
import { callOp, problemOf } from './v2/api'
import type { FollowRow as Rule } from './v2/types'

/** A timer cannot wait longer than this; a later `valid_until` is waited for in steps. */
const LONGEST_WAIT_MS = 2 ** 30

/** The id a follow row's contract has among the list's items: negative, so it is never an OpenAlgo item's. */
export const itemIdOf = (rowId: number): number => -rowId
/** A contract the open sheet stays on after its row has moved on: its own id, never a row's. */
export const SHEET_BASE = 1_000_000_000

/** One follow row as the page draws it. */
export interface FollowState extends RowState {
  id: number
  /** The contract's place among the live rows, or null while the row has none. */
  item: WatchlistItem | null
}

/**
 * The follow rows of the open list (F11): each row's resolved contract, kept current by asking the
 * server (`resolve`) only when something says the contract may have changed. Nothing here reads on a
 * timer: the asks are a new row, a tick outside the band, `valid_until` (one timeout, aimed at that
 * instant), a reconnect, the page coming back, and the first tick after "waiting".
 */
export function useFollowRows(rules: readonly Rule[] | undefined, enabled: boolean) {
  const resolver = useRef<FollowResolver | null>(null)
  if (resolver.current === null)
    resolver.current = new FollowResolver({
      send: async (rows, connected) => (await callOp('resolve', { rows, connected })).rows,
      now: () => Date.now(),
      problem: (error) => problemOf(error).message,
    })
  const core = resolver.current
  const rev = useSyncExternalStore(core.subscribe, core.getRev)

  const live = enabled ? rules : undefined
  useEffect(() => {
    core.sync(live ?? [])
  }, [core, live])
  useEffect(() => {
    core.revive()
    return () => core.dispose()
  }, [core])

  // Returning to the page: the price may have moved while it was away.
  useEffect(() => {
    if (!enabled) return
    const back = () => {
      if (document.visibilityState === 'visible') void core.ask('return')
    }
    document.addEventListener('visibilitychange', back)
    return () => document.removeEventListener('visibilitychange', back)
  }, [core, enabled])

  const [step, setStep] = useState(0)
  // The roll: one timeout aimed at the earliest `valid_until`, set again when an answer changes it.
  // biome-ignore lint/correctness/useExhaustiveDependencies: rev says when an answer changed
  useEffect(() => {
    if (!enabled) return
    const next = core.nextValid(Date.now())
    if (next === null) return
    const timer = setTimeout(
      () => {
        const due = core.due(Date.now())
        if (due.length > 0) void core.ask('valid', due)
        else setStep((count) => count + 1) // too far for one timeout: aim again
      },
      Math.min(next - Date.now(), LONGEST_WAIT_MS)
    )
    return () => clearTimeout(timer)
  }, [core, enabled, rev, step])

  const states = useMemo<FollowState[]>(
    () =>
      (rev >= 0 ? (live ?? []) : []).flatMap((rule) => {
        const row = core.rows.get(rule.id)
        return row
          ? [
              {
                ...row,
                id: rule.id,
                item: row.shown
                  ? {
                      id: itemIdOf(rule.id),
                      symbol: row.shown.contract.symbol,
                      exchange: row.shown.contract.exchange,
                      position: 0,
                    }
                  : null,
              },
            ]
          : []
      }),
    [core, live, rev]
  )
  return { core, states, rev }
}

/**
 * The follow rows' view of the socket: each underlying's latest tick goes to the resolver, which
 * compares it with the row's band. A reconnect (the socket's epoch moving) asks again.
 */
export function useFollowTicks(
  core: FollowResolver,
  lines: ReadonlyMap<string, SymbolData>,
  feed: FeedState,
  enabled: boolean
) {
  const epoch = useRef(feed.epoch)
  useEffect(() => {
    if (!enabled) return
    const prices = new Map<string, Tick>()
    for (const row of core.rows.values()) {
      const key = underlyingKey(row.rule)
      const tick = priceOf(lines.get(key))
      if (tick?.live) prices.set(key, { price: tick.ltp, at: tick.at })
    }
    core.tick(prices, feed.connected)
  }, [core, lines, feed.connected, enabled])
  useEffect(() => {
    if (epoch.current === feed.epoch) return
    epoch.current = feed.epoch
    if (enabled) void core.ask('reconnect')
  }, [core, feed.epoch, enabled])
}
