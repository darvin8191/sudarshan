import type { Socket } from 'socket.io-client'

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
 */

/** How often a connection that is meant to be open, and is not, is asked to connect. */
export const RETRY_MS = 10_000

/** The pause before each new try after a refused connect; the last one repeats. */
export const REFUSED_RETRY_MS = [1_000, 2_000, 5_000, 10_000, 30_000] as const

/** What a refused connect learns from the server about the session. */
export type SessionState = 'valid' | 'ended' | 'unknown'

interface RefusalHandlers {
  /** Asks whether the session still holds. */
  askSession: () => Promise<SessionState>
  /** Leaves for the login page, as a page read's 401 does. */
  toLogin: () => void
}

async function askSession(): Promise<SessionState> {
  try {
    // A route behind the session check: an ended session answers 401 and is cleared on the server.
    const response = await fetch('/auth/active-sessions', {
      credentials: 'include',
      headers: { Accept: 'application/json' },
    })
    if (response.status === 401) return 'ended'
    return response.ok ? 'valid' : 'unknown'
  } catch {
    return 'unknown'
  }
}

const defaultHandlers: RefusalHandlers = {
  askSession,
  toLogin: () => {
    window.location.href = '/login'
  },
}

let handlers: RefusalHandlers = defaultHandlers

/** Tests only: how a refused connect asks about the session, and where an ended one goes. */
export function setRefusalHandlersForTests(next: Partial<RefusalHandlers> | null): void {
  handlers = next ? { ...defaultHandlers, ...next } : defaultHandlers
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
    const pause = REFUSED_RETRY_MS[Math.min(refusals, REFUSED_RETRY_MS.length - 1)]
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
    void handlers.askSession().then((state) => {
      if (stopped || socket.connected) return
      if (state === 'ended') handlers.toLogin()
      else retryLater()
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
