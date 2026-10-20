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
 * it is online again, and every 10 seconds while it stays down. Asking while
 * Socket.IO is already retrying changes nothing.
 */

/** How often a connection that is meant to be open, and is not, is asked to connect. */
export const RETRY_MS = 10_000

interface Shared {
  socket: Socket
  holders: number
  stop: () => void
}

let shared: Shared | null = null

function keepConnected(socket: Socket): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null

  const nudge = () => {
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
  const shown = () => {
    if (document.visibilityState === 'visible') nudge()
  }

  socket.on('disconnect', watch)
  socket.on('connect', settle)
  document.addEventListener('visibilitychange', shown)
  window.addEventListener('online', nudge)

  return () => {
    settle()
    socket.off('disconnect', watch)
    socket.off('connect', settle)
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
