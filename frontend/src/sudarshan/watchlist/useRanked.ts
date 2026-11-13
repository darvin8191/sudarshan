import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import { callOp, problemOf } from './v2/api'
import {
  HOLD_MS,
  parseRankedRef,
  RETURN_AFTER_MS,
  shortName,
  UPDATED_MS,
  underlyingOf,
  updatedLine,
} from './v2/ranked'
import type { Ranked, Row } from './v2/types'

/**
 * M8.9 F16: the one reader of a ranked list. A ranking is read when the owner opens the list,
 * when he returns to the screen after more than 15 s away, when he pulls to refresh and from the
 * Refresh ranking button, and at no other moment: there is no timer here that asks for anything,
 * and a hidden page asks for nothing (a read owed to a page opened while hidden is made when it
 * is shown). A pull or the button inside a minute of the last read shows that read, as the server's
 * own 60 s cache would, with no request. Rows move only when an answer arrives; a price tick never
 * reaches this file.
 */
export type RankSize = 20 | 50

/** The states whose rows are a ranking worth keeping for a minute; the others are asked again. */
const HOLDS = new Set(['ok', 'stale', 'closed'])

export interface Ranking {
  /** The last answer, or null before the first. */
  data: Ranked | null
  /** A read that failed outright, in the server's words (the old answer stays). */
  problem: string | null
  reading: boolean
  /** When this phone received the last answer (ms), for "Read 20 s ago". */
  got: number | null
  /** A pull or the button was answered with the cached read. */
  held: boolean
  size: RankSize
  setSize: (size: RankSize) => void
  /** Every row of the answer, in Upstox's order. */
  rows: readonly Row[]
  /** The rows shown (the underlying filter applied), as the list's items, with stable ids. */
  items: WatchlistItem[]
  /** EXCHANGE:SYMBOL -> the ranking's own figure, as read. */
  metrics: ReadonlyMap<string, string>
  underlying: string | null
  setUnderlying: (name: string | null) => void
  /** "Ranking updated 10:43 · 3 new: ...", for a minute after a read that changed the order. */
  updated: string | null
  /** The owner's pull, or the button. */
  pull: () => void
  retry: () => void
}

const visible = () => typeof document === 'undefined' || document.visibilityState !== 'hidden'

export function useRanked(ref: string | null): Ranking {
  const parts = parseRankedRef(ref)
  const src = parts?.src
  const asset = parts?.asset
  const cat = parts?.cat
  const [data, setData] = useState<Ranked | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [reading, setReading] = useState(false)
  const [got, setGot] = useState<number | null>(null)
  const [held, setHeld] = useState(false)
  const [size, setSizeState] = useState<RankSize>(20)
  const [underlying, setUnderlying] = useState<string | null>(null)
  const [updated, setUpdated] = useState<string | null>(null)
  const sequence = useRef(0)
  const latest = useRef<{
    data: Ranked | null
    got: number | null
    size: RankSize
    answered: RankSize
  }>({ data: null, got: null, size: 20, answered: 20 })
  const owed = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const ids = useRef(new Map<string, number>())

  const read = useCallback(
    async (wanted: RankSize) => {
      if (!src || !asset || !cat) return
      const mine = ++sequence.current
      setReading(true)
      setHeld(false)
      try {
        const answer = await callOp('ranked', { src, asset, cat, size: wanted })
        if (mine !== sequence.current) return
        const stamp = Date.now()
        const before = latest.current.data
        const sameSize = latest.current.answered === wanted
        latest.current = { data: answer, got: stamp, size: wanted, answered: wanted }
        setData(answer)
        // Review L16: an underlying the new ranking does not hold no longer filters it to nothing.
        setUnderlying((picked) =>
          picked !== null &&
          !answer.rows.some(
            (row) => underlyingOf(row.contract.symbol, row.contract.exchange) === picked
          )
            ? null
            : picked
        )
        setGot(stamp)
        setProblem(null)
        const was = before?.rows.map((row) => row.contract.key) ?? []
        const now = answer.rows.map((row) => row.contract.key)
        if (before && was.length > 0 && sameSize && now.join() !== was.join()) {
          const known = new Set(was)
          const fresh = answer.rows
            .filter((row) => !known.has(row.contract.key))
            .map((row) => shortName(row.contract.symbol, row.contract.exchange))
          if (timer.current) clearTimeout(timer.current)
          setUpdated(updatedLine(answer.read_at ?? stamp, fresh))
          timer.current = setTimeout(() => setUpdated(null), UPDATED_MS)
        }
      } catch (error) {
        if (mine === sequence.current) setProblem(problemOf(error).message)
      } finally {
        if (mine === sequence.current) setReading(false)
      }
    },
    [src, asset, cat]
  )

  /** Ask for a read, unless the page is hidden: then it is owed until the page is shown. */
  const ask = useCallback(
    (wanted: RankSize) => {
      if (!visible()) {
        owed.current = true
        return
      }
      owed.current = false
      void read(wanted)
    },
    [read]
  )

  // Opening the list reads it once.
  useEffect(() => {
    if (!src) return
    latest.current = { data: null, got: null, size: 20, answered: 20 }
    ids.current = new Map()
    setData(null)
    setGot(null)
    setProblem(null)
    setHeld(false)
    setSizeState(20)
    setUnderlying(null)
    setUpdated(null)
    ask(20)
    return () => {
      sequence.current += 1
      if (timer.current) clearTimeout(timer.current)
    }
  }, [src, ask])

  // Coming back to the screen after more than 15 s away reads once; a short look away reads nothing.
  useEffect(() => {
    if (!src || typeof document === 'undefined') return
    let hiddenAt: number | null = null
    const change = () => {
      if (!visible()) {
        hiddenAt = Date.now()
        return
      }
      const away = hiddenAt === null ? 0 : Date.now() - hiddenAt
      hiddenAt = null
      if (owed.current || away > RETURN_AFTER_MS) ask(latest.current.size)
    }
    document.addEventListener('visibilitychange', change)
    return () => document.removeEventListener('visibilitychange', change)
  }, [src, ask])

  const pull = useCallback(() => {
    const { data: last, got: at, size: wanted } = latest.current
    if (last && at !== null && HOLDS.has(last.state) && Date.now() - at < HOLD_MS) {
      setHeld(true)
      return
    }
    ask(wanted)
  }, [ask])

  const setSize = useCallback(
    (next: RankSize) => {
      setSizeState(next)
      latest.current.size = next
      ask(next)
    },
    [ask]
  )

  const rows = data?.rows ?? NO_ROWS
  const shown = useMemo(
    () =>
      underlying === null
        ? rows
        : rows.filter(
            (row) => underlyingOf(row.contract.symbol, row.contract.exchange) === underlying
          ),
    [rows, underlying]
  )
  // A contract keeps its id from the first read that showed it, so an open sheet stays on its
  // contract when a later read moves it; ids are never reused within one ranking.
  const items = useMemo<WatchlistItem[]>(
    () =>
      shown.map((row, index) => {
        const key = row.contract.key.toUpperCase()
        let id = ids.current.get(key)
        if (id === undefined) {
          id = ids.current.size + 1
          ids.current.set(key, id)
        }
        return { id, symbol: row.contract.symbol, exchange: row.contract.exchange, position: index }
      }),
    [shown]
  )
  const metrics = useMemo(
    () =>
      new Map(
        rows.flatMap((row) =>
          row.metric
            ? [
                [
                  `${row.contract.exchange}:${row.contract.symbol}`.toUpperCase(),
                  row.metric,
                ] as const,
              ]
            : []
        )
      ),
    [rows]
  )

  return {
    data,
    problem,
    reading,
    got,
    held,
    size,
    setSize,
    rows,
    items,
    metrics,
    underlying,
    setUnderlying,
    updated,
    pull,
    retry: pull,
  }
}

const NO_ROWS: readonly Row[] = []
