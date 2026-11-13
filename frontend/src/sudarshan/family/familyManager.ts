// SUDARSHAN P6 D2: the family market manager. A MarketDataManager of its own whose ticks come
// from the family viewer socket (`/family/ws`), so `useFrameLines`, `useFramePrices` and
// `LiveLine` run unchanged on the viewer's screens (design 2.5).
//
// What it never does: ask the owner's `/api/websocket/apikey`, `/api/websocket/config` or
// `/api/config/host`, or fall back to the owner's REST quotes. The session is the family
// cookie, which the browser sends with the handshake.
//
// Besides ticks the socket carries two things the manager does not read itself, both kept here:
// `feed_state` (is the server's own price feed up, reconnecting, or is the market closed) and
// `book` (the service has read a part of the books again: refetch it at once, cross-design Y11).
import { useSyncExternalStore } from 'react'
import { MarketDataManager, type SubscriptionMode } from '@/lib/MarketDataManager'
import { announceSignedOut } from './api'
import { type BookPart, type FeedState, type Screen, WS_CLOSE_SIGNED_OUT, WS_PATH } from './types'

export interface FamilyFeed {
  /** What the server's feed says; null before it has said anything on this socket. */
  state: FeedState | null
  since: number | null
}

const NOT_YET: FamilyFeed = { state: null, since: null }

let manager: MarketDataManager | null = null
let socket: WebSocket | null = null
let screen: Screen | null = null
let feed: FamilyFeed = NOT_YET
const feedListeners = new Set<() => void>()
const bookListeners = new Set<(part: BookPart, readAt: number) => void>()

function setFeed(next: FamilyFeed): void {
  if (next.state === feed.state && next.since === feed.since) return
  feed = next
  for (const listener of [...feedListeners]) listener()
}

function url(): string {
  const { protocol, host } = window.location
  return `${protocol === 'https:' ? 'wss:' : 'ws:'}//${host}${WS_PATH}`
}

function sendScreen(): void {
  if (!socket || socket.readyState !== WebSocket.OPEN || screen === null) return
  socket.send(JSON.stringify({ action: 'screen', name: screen }))
}

const PARTS: readonly BookPart[] = ['positions', 'holdings', 'orders', 'trades', 'funds']
const STATES: readonly FeedState[] = ['realtime', 'reconnecting', 'closed']

function onMessage(message: Record<string, unknown>): void {
  if (message.type === 'feed_state') {
    const state = STATES.find((known) => known === message.state)
    if (state) {
      setFeed({ state, since: typeof message.since === 'number' ? message.since : null })
    }
  } else if (message.type === 'book') {
    const part = PARTS.find((known) => known === message.part)
    if (part) {
      const readAt = typeof message.read_at === 'number' ? message.read_at : Date.now()
      for (const listener of [...bookListeners]) listener(part, readAt)
    }
  }
}

/** The one family manager of this page. `getInstance()` stays the owner's and is never touched. */
export function getFamilyManager(): MarketDataManager {
  if (!manager) {
    manager = MarketDataManager.create({
      url,
      authenticate: (opened) => {
        socket = opened
        opened.send(JSON.stringify({ action: 'authenticate', family: true }))
        // The screen follows the sign-in on the same socket, in order.
        sendScreen()
      },
      // The viewer socket carries last price only, whatever a screen asks for.
      mode: (_mode: SubscriptionMode) => 1,
      onMessage,
      stopAfter: [WS_CLOSE_SIGNED_OUT],
      onClose: (code) => {
        socket = null
        // What the server's feed said is not known any more once our own link to it is gone.
        setFeed(NOT_YET)
        if (code === WS_CLOSE_SIGNED_OUT) announceSignedOut()
      },
    })
  }
  return manager
}

/** Tell the service which screen is open: its feed claims follow the screens (design 2.5). */
export function setFamilyScreen(name: Screen | null): void {
  screen = name
  sendScreen()
}

/** The server's feed state, as a hook. */
export function useFamilyFeed(): FamilyFeed {
  return useSyncExternalStore(
    (listener) => {
      feedListeners.add(listener)
      return () => feedListeners.delete(listener)
    },
    () => feed
  )
}

/** A part of the books was read again by the service: refetch it now. */
export function onFamilyBook(listener: (part: BookPart, readAt: number) => void): () => void {
  bookListeners.add(listener)
  return () => bookListeners.delete(listener)
}

/** For tests only: drop the manager and everything it kept. */
export function resetFamilyManagerForTests(): void {
  manager?.disconnect()
  manager = null
  socket = null
  screen = null
  feed = NOT_YET
  feedListeners.clear()
  bookListeners.clear()
}
