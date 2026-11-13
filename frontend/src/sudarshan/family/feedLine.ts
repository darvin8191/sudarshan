// SUDARSHAN P6 D4: what the viewer's status line says, from two sources: the ticks that arrive
// on the viewer socket (the shipped `feedLine` rules) and what the server's own feed says about
// itself (`feed_state` on the same socket). Rule 7: "Real-time" only while ticks arrive; a screen
// that cannot be live says so with a marker and a label; old numbers are never shown as current.
import type { FeedLine, MarketOpen } from '@/sudarshan/dashboard/live'
import type { FeedState } from './types'

/**
 * The line to show. A tick that arrived is real-time whatever the server says. Otherwise:
 *  - the server says the market is closed: "Market closed" (nothing is due);
 *  - the server says its feed is reconnecting: "Reconnecting" with the age of the figures, even
 *    while this phone's own socket is up, because no tick can be coming;
 *  - else the line as the ticks make it.
 */
export function withFeedState(line: FeedLine, state: FeedState | null): FeedLine {
  if (line.kind === 'idle' || line.kind === 'live') return line
  if (state === 'closed') return { kind: 'closed' }
  if (state === 'reconnecting' && line.kind !== 'connecting') {
    return {
      kind: 'connecting',
      at: line.kind === 'delayed' || line.kind === 'polling' ? line.at : null,
      stale: true,
      again: true,
    }
  }
  return line
}

/**
 * Open, closed, or not known, for every exchange alike: the viewer has the server's feed state
 * and not the exchanges' timings (those are an owner read). Not known until the socket has said.
 */
export function familyMarketOpen(state: FeedState | null): MarketOpen {
  return () => (state === null ? null : state !== 'closed')
}
