import type { Socket } from 'socket.io-client'
import { forgetApiKey } from '@/sudarshan/signout'
import { claimTrip } from '@/sudarshan/trips'

/**
 * One Socket.IO connection for the signed-in tab, not one per layout or page.
 *
 * Upstream opened the connection inside the layout's provider and closed it
 * whenever the provider's effect ran again. That effect depended on the
 * router's `navigate`, whose identity changes with every pathname, so every
 * page change (and every move between /trading and a normal page, which swaps
 * the layout) closed the connection and opened a new one with `forceNew`. A
 * fill, rejection or square-off the server pushed during that handshake went
 * to nobody.
 *
 * Here the provider borrows the connection. A provider that unmounts gives it
 * back, and the connection closes only when nothing has taken it again by the
 * end of the current task: React unmounts the old layout and mounts the new
 * one in the same commit, so a page change never closes it, while a logout or
 * leaving the signed-in pages does.
 *
 * It also keeps trying. Socket.IO gives up after five attempts; a phone that
 * lost its network for half a minute would then never hear about an order
 * again until a reload. A connection that is meant to be open and is not is
 * asked to connect when the page comes back into view, when the browser says
 * it is online again, and every 10 seconds while it stays down: from a drop
 * or from a first connect that failed (a tab opened while the server
 * restarts, or on a bad network) until it is up, through and past the point
 * where Socket.IO itself gives up. Asking while Socket.IO is already retrying
 * changes nothing.
 *
 * It counts how often the connection has come up, so a book can tell the
 * first connect (nothing was missed: the page has just read it) from a
 * reconnect (whatever was pushed meanwhile went to nobody).
 *
 * A connect the server refuses (SIO1: only the owner's valid session may
 * connect) is final for Socket.IO, which stops trying. A refusal marked
 * `retry` (the server could not check the session just then) is asked again
 * after a pause that grows to 30 s. Any other refusal first asks the server,
 * with an ordinary read, whether the session still holds: when it has ended,
 * that read's 401 clears it as every page read's would and the tab goes to the
 * login page; otherwise the connect is asked again after the same pause. A
 * connection the server closes on purpose (a session's end, the daily
 * rollover) gets the same question, since Socket.IO does not reconnect those.
 *
 * Each pause is spread by up to half either way, so tabs refused together do
 * not come back together. The read is given 10 s, and no answer counts as
 * "unknown". The tab goes to the login page by itself at most once a minute
 * (`trips.ts`), whatever the server answers. A trip that would repeat is not
 * taken: the order updates line says "Sign in again" with a button instead.
 */

/** How often a connection that is meant to be open, and is not, is asked to connect. */
export const RETRY_MS = 10_000

/** The pause before each new try after a refused connect (spread ±50 %); the last one repeats. */
export const REFUSED_RETRY_MS = [1_000, 2_000, 5_000, 10_000, 30_000] as const

/** How long the session read may take; past it the answer is "unknown". */
export const SESSION_READ_TIMEOUT_MS = 10_000

/** What a refused connect learns from the server about the session. */
export type SessionState = 'valid' | 'ended' | 'unknown'

interface RefusalHandlers {
  /** Asks whether the session still holds. */
  askSession: () => Promise<SessionState>
  /** Leaves for the login page, as a page read's 401 does. */
  toLogin: () => void
  /** A number in [0, 1), which spreads each pause. */
  random: () => number
}

async function askSession(): Promise<SessionState> {
  // The read is let go at the time limit too, so it holds none of the browser's connections.
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), SESSION_READ_TIMEOUT_MS)
  try {
    // A route behind the session check: an ended session answers 401 and is cleared on the server.
    const response = await fetch('/auth/active-sessions', {
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })
    if (response.status === 401) return 'ended'
    return response.ok ? 'valid' : 'unknown'
  } catch {
    return 'unknown'
  } finally {
    clearTimeout(timer)
  }
}

/** The session read's answer, or "unknown" once the time limit has passed without one. */
function withinTimeLimit(read: Promise<SessionState>): Promise<SessionState> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve('unknown'), SESSION_READ_TIMEOUT_MS)
    read.then(
      (state) => {
        clearTimeout(timer)
        resolve(state)
      },
      () => {
        clearTimeout(timer)
        resolve('unknown')
      }
    )
  })
}

const defaultHandlers: RefusalHandlers = {
  askSession,
  toLogin: () => {
    window.location.href = '/login'
  },
  random: Math.random,
}

let handlers: RefusalHandlers = defaultHandlers

let signInNeeded = false
const signInListeners = new Set<() => void>()

function setSignInNeeded(next: boolean): void {
  if (signInNeeded === next) return
  signInNeeded = next
  for (const listener of [...signInListeners]) listener()
}

/**
 * Whether the session has ended and the tab may not go to the login page by itself just now (it
 * went less than a minute ago): the order updates line then asks the owner to sign in again.
 */
export function signInNeededNow(): boolean {
  return signInNeeded
}

/** Calls `listener` whenever signInNeededNow() changes; returns the way to stop. */
export function subscribeSignInNeeded(listener: () => void): () => void {
  signInListeners.add(listener)
  return () => {
    signInListeners.delete(listener)
  }
}

/** The owner's own way to the login page (the line's button): not limited, he asked. */
export function goToLogin(): void {
  handlers.toLogin()
}

/** Tests only: how a refused connect asks, where it goes and how it spreads; null resets all. */
export function setRefusalHandlersForTests(next: Partial<RefusalHandlers> | null): void {
  handlers = next ? { ...defaultHandlers, ...next } : defaultHandlers
  if (!next) setSignInNeeded(false)
}

/** Tests only: say the session has ended and the trip is spent, or that it has not. */
export function setSignInNeededForTests(next: boolean): void {
  setSignInNeeded(next)
}

interface Shared {
  socket: Socket
  holders: number
  stop: () => void
}

let shared: Shared | null = null

/** How many times each connection has come up. */
const connects = new WeakMap<Socket, number>()

/**
 * How many times this connection has come up, or undefined for one this
 * module did not open. 1 during the first connect; more on a reconnect.
 */
export function connectionCount(socket: Socket): number | undefined {
  return connects.get(socket)
}

function keepConnected(socket: Socket): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null
  let refusedTimer: ReturnType<typeof setTimeout> | null = null
  let refusals = 0
  let stopped = false

  const retryNow = () => {
    if (refusedTimer) clearTimeout(refusedTimer)
    refusedTimer = null
    if (!stopped && !socket.connected) socket.connect()
  }
  const retryLater = () => {
    if (stopped) return
    const base = REFUSED_RETRY_MS[Math.min(refusals, REFUSED_RETRY_MS.length - 1)]
    const pause = Math.round(base * (0.5 + handlers.random())) // half to one and a half
    refusals += 1
    if (refusedTimer) clearTimeout(refusedTimer)
    refusedTimer = setTimeout(retryNow, pause)
  }
  const refused = (retry: boolean) => {
    // A transport failure leaves the socket active: Socket.IO and watch() retry that one.
    if (stopped || socket.active || socket.connected) return
    if (retry) {
      retryLater()
      return
    }
    void withinTimeLimit(handlers.askSession()).then((state) => {
      if (stopped || socket.connected) return
      if (state !== 'ended') {
        retryLater()
        return
      }
      forgetApiKey() // SUDARSHAN (review M2): the session has ended, so its API key goes too
      if (claimTrip('login')) handlers.toLogin()
      else setSignInNeeded(true) // it went less than a minute ago: the line asks instead
    })
  }

  const nudge = () => {
    if (refusedTimer) {
      retryNow() // a refused connect waiting for its next try: now, the phone is back
      return
    }
    if (socket.active && !socket.connected) socket.connect()
  }
  const watch = () => {
    if (timer) return
    timer = setTimeout(function again() {
      timer = null
      if (!socket.active || socket.connected) return
      nudge()
      timer = setTimeout(again, RETRY_MS)
    }, RETRY_MS)
  }
  const settle = () => {
    if (timer) clearTimeout(timer)
    timer = null
  }
  const connected = () => {
    connects.set(socket, (connects.get(socket) ?? 0) + 1)
    settle()
    setSignInNeeded(false)
    refusals = 0
    if (refusedTimer) clearTimeout(refusedTimer)
    refusedTimer = null
  }
  const failed = (error?: Error) => {
    watch()
    // A refusal's data, when the server could not check the session just then: {retry: true}.
    const data = (error as (Error & { data?: { retry?: unknown } }) | undefined)?.data
    refused(data?.retry === true)
  }
  const dropped = (reason: Socket.DisconnectReason) => {
    watch()
    // The server closed this connection on purpose (a session's end, the daily rollover), and
    // Socket.IO will not reconnect it: the same question as for a refusal decides.
    if (reason === 'io server disconnect') refused(false)
  }
  const shown = () => {
    if (document.visibilityState === 'visible') nudge()
  }

  // Every failed attempt raises connect_error, the first connect's included,
  // and the loop above keeps asking until the connection is up, so it runs on
  // after Socket.IO's last attempt (reconnect_failed) without a listener there.
  socket.on('connect', connected)
  socket.on('disconnect', dropped)
  socket.on('connect_error', failed)
  document.addEventListener('visibilitychange', shown)
  window.addEventListener('online', nudge)

  return () => {
    stopped = true
    settle()
    if (refusedTimer) clearTimeout(refusedTimer)
    refusedTimer = null
    socket.off('connect', connected)
    socket.off('disconnect', dropped)
    socket.off('connect_error', failed)
    document.removeEventListener('visibilitychange', shown)
    window.removeEventListener('online', nudge)
  }
}

/**
 * The tab's connection, opened with `create` when there is none to share or
 * the one there is was closed for good (by the server or on purpose).
 */
export function acquireSharedSocket(create: () => Socket): Socket {
  if (shared && !shared.socket.active && !shared.socket.connected) {
    shared.stop()
    shared = null
  }
  if (!shared) {
    setSignInNeeded(false) // a new connection has asked nothing yet (a sign-in with no reload)
    const socket = create()
    shared = { socket, holders: 0, stop: keepConnected(socket) }
  }
  shared.holders += 1
  return shared.socket
}

/**
 * Give the connection back. It closes once nothing holds it at the end of the
 * current task, so a provider remounting in the same commit keeps it open.
 */
export function releaseSharedSocket(socket: Socket): void {
  if (!shared || shared.socket !== socket) {
    socket.disconnect()
    return
  }
  shared.holders = Math.max(0, shared.holders - 1)
  if (shared.holders > 0) return
  const released = shared
  queueMicrotask(() => {
    if (shared !== released || released.holders > 0) return
    released.stop()
    released.socket.disconnect()
    shared = null
  })
}
