/**
 * The phone chart's price header, outside React: the latest price, held where
 * a tick can change it without re-rendering the page around it, and the line
 * under it that says when the last tick came.
 */
import { type LiveStatus, opensAtWords } from '@/lib/trading/liveStatus'
import type { PhoneTick } from './phoneChart'

/** The latest price, and the latest one the stream delivered. */
export interface Prices {
  tick: PhoneTick | null
  streamed: PhoneTick | null
}

/** Runs a callback before the next paint, or soon where a page has no paints. */
export type FrameScheduler = (run: () => void) => void

const nextFrame: FrameScheduler = (run) => {
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => run())
  else setTimeout(run, 16)
}

/**
 * Holds the price for `useSyncExternalStore`, so a tick re-renders only what
 * reads it. The value is current at once; its readers hear once per frame, so
 * a burst of ticks between two paints renders the header once, at the last.
 */
export class PriceStore {
  private value: Prices = { tick: null, streamed: null }
  private readonly listeners = new Set<() => void>()
  private readonly schedule: FrameScheduler
  private pending = false

  constructor(schedule: FrameScheduler = nextFrame) {
    this.schedule = schedule
  }

  readonly get = (): Prices => this.value

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  set(tick: PhoneTick): void {
    this.value = { tick, streamed: tick.polled ? this.value.streamed : tick }
    if (this.pending) return
    this.pending = true
    this.schedule(() => {
      this.pending = false
      for (const listener of [...this.listeners]) listener()
    })
  }
}

const clockSeconds = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

/**
 * The line under the price. In a closed market, when it opens: a snapshot
 * the feed sends after hours is not news. Otherwise the time of the last
 * trade where the feed says when it was, so a book that moves while nothing
 * trades does not pass for trading; else when the last tick came (the
 * exchange's time for it where the feed sent one). An index is computed, not
 * traded, so its line is always the last tick. In India time either way.
 */
export function tickLine(
  streamed: PhoneTick | null,
  status: LiveStatus | null,
  now = Date.now(),
  traded = true
): string {
  if (status?.line.kind === 'closed')
    return status.opensAt === null
      ? 'Prices from the last session'
      : `Opens ${opensAtWords(status.opensAt, now)}`
  if (traded && streamed?.tradeTime != null)
    return `Last trade ${clockSeconds.format(new Date(streamed.tradeTime * 1000))}`
  if (streamed) {
    const at = streamed.time !== null ? streamed.time * 1000 : streamed.at
    return `Last tick ${clockSeconds.format(new Date(at))}`
  }
  return 'Waiting for the first tick'
}
