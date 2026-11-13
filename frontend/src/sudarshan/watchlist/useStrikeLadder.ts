import axios from 'axios'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ASK_GAP_MS, atmOf, EACH_SIDE } from './strikePicker'
import { callOp, problemOf } from './v2/api'
import type { Expiry, Strikes } from './v2/types'

export interface Ladder {
  /** The answer for the tab on screen; null while it is being read (its contracts are not followed then). */
  data: Strikes | null
  /** The expiries as the server last listed them, kept while another tab is read. */
  expiries: readonly Expiry[]
  /** The tab on screen: the one picked, else the server's own. */
  tab: string | null
  loading: boolean
  /** The server's sentence for what could not be read, or null. */
  error: string | null
  /** The server's words about an expiry that ended while the picker was open, or null. */
  ended: string | null
  choose: (date: string) => void
  retry: () => void
  /** The underlying's price as it stands, called as it moves. Asks the server again only when it must. */
  observe: (price: number | null) => void
}

interface Mind {
  target: { underlying: string; exchange: string } | null
  price: number | null
  /** The price the answer on screen was asked with (null when none was sent). */
  askedWith: number | null
  chosen: string | null
  data: Strikes | null
  flying: AbortController | null
  lastAt: number
  timer: ReturnType<typeof setTimeout> | null
}

const NONE: readonly Expiry[] = []

/**
 * The strike picker's ladder (M8.18, F14): the server's `strikes` op for one underlying, read when the
 * picker opens, when another tab is chosen, when an expiry has ended, and when the underlying's price moves
 * past the midpoint to a neighbouring strike. Never on a timer: a move that comes inside two seconds of the
 * last read waits for those two seconds once (one timeout aimed at the need, set only while there is one),
 * and a price that stays inside its strike's band asks nothing. One read at a time; a tab change takes the
 * place of the one in flight. `p` is the underlying's own price; the first answer goes without one (the
 * ladder's middle, the tabs and the server's words) and the first price asks again.
 */
export function useStrikeLadder(target: { underlying: string; exchange: string } | null): Ladder {
  const key = target ? `${target.exchange}:${target.underlying}` : null
  const mind = useRef<Mind>({
    target: null,
    price: null,
    askedWith: null,
    chosen: null,
    data: null,
    flying: null,
    lastAt: 0,
    timer: null,
  })
  const [view, setView] = useState<{
    data: Strikes | null
    expiries: readonly Expiry[]
    tab: string | null
    loading: boolean
    error: string | null
    ended: string | null
  }>({ data: null, expiries: NONE, tab: null, loading: false, error: null, ended: null })

  const ask = useCallback((exp: string | null) => {
    const m = mind.current
    const t = m.target
    if (!t) return
    m.flying?.abort()
    if (m.timer !== null) clearTimeout(m.timer)
    m.timer = null
    const stop = new AbortController()
    m.flying = stop
    m.lastAt = Date.now()
    const p = m.price
    callOp(
      'strikes',
      {
        u: t.underlying,
        x: t.exchange,
        n: EACH_SIDE,
        ...(exp ? { exp } : {}),
        ...(p !== null ? { p } : {}),
      },
      stop.signal
    )
      .then((data) => {
        if (stop.signal.aborted) return
        m.flying = null
        m.data = data
        m.askedWith = p
        setView((was) => ({
          data,
          expiries: data.expiries,
          tab: exp ?? data.expiry,
          loading: false,
          error: null,
          ended: was.ended,
        }))
      })
      .catch((error: unknown) => {
        if (stop.signal.aborted || axios.isCancel(error)) return
        m.flying = null
        const problem = problemOf(error)
        if (exp && problem.code === 'invalid' && problem.field === 'exp') {
          // The expiry ended while the picker was open: read the ladder again, in the server's words.
          m.chosen = null
          setView((was) => ({ ...was, ended: problem.message }))
          ask(null)
          return
        }
        setView((was) => ({ ...was, loading: false, error: problem.message }))
      })
  }, [])

  const decide = useCallback(() => {
    const m = mind.current
    const d = m.data
    if (m.price === null || !d || m.flying) return
    const need =
      m.price !== m.askedWith &&
      atmOf(
        m.price,
        d.strikes.map((each) => each.strike)
      ) !== d.atm &&
      d.strikes.length > 0
    if (!need) return
    const wait = m.lastAt + ASK_GAP_MS - Date.now()
    if (wait > 0) {
      if (m.timer === null)
        m.timer = setTimeout(() => {
          m.timer = null
          decide()
        }, wait)
      return
    }
    ask(m.chosen)
  }, [ask])

  // biome-ignore lint/correctness/useExhaustiveDependencies: key is the identity of the target
  useEffect(() => {
    const m = mind.current
    m.target = target
    m.price = null
    m.askedWith = null
    m.chosen = null
    m.data = null
    setView({
      data: null,
      expiries: NONE,
      tab: null,
      loading: target !== null,
      error: null,
      ended: null,
    })
    if (!target) return
    ask(null)
    return () => {
      m.flying?.abort()
      m.flying = null
      if (m.timer !== null) clearTimeout(m.timer)
      m.timer = null
    }
  }, [key, ask])

  const choose = useCallback(
    (date: string) => {
      const m = mind.current
      if (m.chosen === date || (m.chosen === null && m.data?.expiry === date)) return
      m.chosen = date
      m.data = null
      setView((was) => ({
        ...was,
        data: null,
        tab: date,
        loading: true,
        error: null,
        ended: null,
      }))
      ask(date)
    },
    [ask]
  )
  const retry = useCallback(() => {
    setView((was) => ({ ...was, loading: true, error: null }))
    ask(mind.current.chosen)
  }, [ask])
  const observe = useCallback(
    (price: number | null) => {
      mind.current.price = price
      decide()
    },
    [decide]
  )
  return { ...view, choose, retry, observe }
}
