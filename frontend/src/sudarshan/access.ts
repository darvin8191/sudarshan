/**
 * SUDARSHAN: whether a screen can read prices, and if not, why. One answer for
 * every screen that reads the broker with the API key: the watchlist, the
 * phone chart, the dashboard and the phone books. Four states:
 *
 * - checking: no session check has answered yet and no key is known, so
 *   nothing is claimed (never "no API key" while it is not known).
 * - upstox-logged-out: the server says the app session is fine and today's
 *   Upstox login is not (the token rolls over at 03:30, before a new login).
 * - no-api-key: Upstox is logged in and the account has no API key.
 * - ready: a key, and no word from the server that today's login has lapsed.
 *
 * A key from an earlier answer counts until the server says otherwise: the
 * key does not roll over, only the broker login does. While a screen cannot
 * read prices, it asks the server again (`syncSession`) when it opens, when
 * the page comes back into view and every 30 seconds while in view, so a
 * login made elsewhere (auto-login's schedule, Test now, another phone, a new
 * API key) reaches it without a reload. Never while it can: nothing is polled
 * on a working screen.
 *
 * A ready screen asks once more, and only on an event, never on a timer: when
 * the page comes back into view (the phone woke after the 03:00 expiry) and
 * when the market socket signs in again. At most once every MIN_GAP_MS. If the
 * server says the login lapsed or the key is gone, every screen moves to its
 * right state by itself.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { syncSession } from '@/components/auth/AuthSync'
import type { MarketDataManager } from '@/lib/MarketDataManager'
import { useAuthStore } from '@/stores/authStore'
import { useMarketDataManager } from '@/sudarshan/live/useFrameLines'

export type PriceAccess =
  | { state: 'ready'; apiKey: string }
  | { state: 'checking' | 'upstox-logged-out' | 'no-api-key'; apiKey: null }

export type PriceAccessState = PriceAccess['state']

/** What the auth store knows, as the access rule reads it. */
export interface AccessFacts {
  apiKey: string | null | undefined
  sessionChecked?: boolean
  brokerSessionExpired?: boolean
}

export function priceAccessOf({
  apiKey,
  sessionChecked,
  brokerSessionExpired,
}: AccessFacts): PriceAccess {
  // The server's word on the login first: a key without a broker login reads nothing.
  if (brokerSessionExpired === true) return { state: 'upstox-logged-out', apiKey: null }
  if (typeof apiKey === 'string' && apiKey !== '') return { state: 'ready', apiKey }
  if (sessionChecked !== true) return { state: 'checking', apiKey: null }
  return { state: 'no-api-key', apiKey: null }
}

/** How often a screen that cannot read prices asks again, while it is in view. */
export const RECHECK_MS = 30_000

/** The least time between two checks started by a page return or a socket sign-in. */
export const MIN_GAP_MS = 5_000

/** When the last check started. */
let lastAskedAt = 0

/** One check at a time, and none for a page out of view. */
let asking = false
/** Screens open now that cannot read prices. */
let waiting = 0
let timer: ReturnType<typeof setInterval> | undefined

function askAgain(): void {
  if (asking || document.visibilityState === 'hidden') return
  asking = true
  lastAskedAt = Date.now()
  void syncSession({ keepOnFailure: true }).finally(() => {
    asking = false
  })
}

function onVisibility(): void {
  if (document.visibilityState === 'visible') askAgain()
}

/** A check after an event, unless one started a moment ago. */
function askOnEvent(): void {
  if (Date.now() - lastAskedAt < MIN_GAP_MS) return
  askAgain()
}

/** Screens open now, ready or not. */
let watchers = 0
let stopWatching: (() => void) | undefined

function onReturn(): void {
  if (document.visibilityState === 'visible') askOnEvent()
}

/** Ask again when the page returns to view and when the market socket signs in again. */
function watch(manager: MarketDataManager): () => void {
  watchers += 1
  if (watchers === 1) {
    document.addEventListener('visibilitychange', onReturn)
    let epoch: number | undefined
    const removeListener = manager.addStateListener((state) => {
      // The first call reports where the socket already is, not a sign-in.
      if (epoch !== undefined && state.connectionEpoch !== epoch) askOnEvent()
      epoch = state.connectionEpoch
    })
    stopWatching = () => {
      document.removeEventListener('visibilitychange', onReturn)
      removeListener()
    }
  }
  return () => {
    watchers -= 1
    if (watchers > 0) return
    stopWatching?.()
    stopWatching = undefined
  }
}

/** Ask now, then on every return to the page and every RECHECK_MS, until the last screen stops. */
function wait(): () => void {
  waiting += 1
  if (waiting === 1) {
    timer = setInterval(askAgain, RECHECK_MS)
    document.addEventListener('visibilitychange', onVisibility)
  }
  askAgain()
  return () => {
    waiting -= 1
    if (waiting > 0) return
    clearInterval(timer)
    timer = undefined
    document.removeEventListener('visibilitychange', onVisibility)
  }
}

/** The screen's access, with the key to read with only when it is ready. */
export function usePriceAccess(): PriceAccess {
  const { apiKey, sessionChecked, brokerSessionExpired } = useAuthStore(
    useShallow((state) => ({
      apiKey: state.apiKey,
      sessionChecked: state.sessionChecked,
      brokerSessionExpired: state.brokerSessionExpired,
    }))
  )
  const access = useMemo(
    () => priceAccessOf({ apiKey, sessionChecked, brokerSessionExpired }),
    [apiKey, sessionChecked, brokerSessionExpired]
  )
  const blocked = access.state !== 'ready'
  useEffect(() => (blocked ? wait() : undefined), [blocked])
  const manager = useMarketDataManager()
  useEffect(() => watch(manager), [manager])
  return access
}

/**
 * Read once more when the access turns ready, if the last read failed while
 * it was not: a book read with an old key while Upstox was logged out. A page
 * whose key only arrives now reads by itself, so it is not read twice.
 */
export function useReadWhenReady(state: PriceAccessState, failed: boolean, read: () => void) {
  const before = useRef(state)
  useEffect(() => {
    const was = before.current
    before.current = state
    if (state === 'ready' && was !== 'ready' && failed) read()
  }, [state, failed, read])
}
