/**
 * What a table card's line says (design 3.6, the card's line table), as one pure function. The
 * screen's status and the engine line come from the shared state cache, kept current by the state
 * push, so a card never keeps a copy of its own; the card's own words are used only while the
 * state still says what the card was read with. The first row of the table that applies wins:
 * not in the owner's room, Stopped, Paused, the feed down, waiting for history or the master,
 * the market closed, a check after the close, then the price rows: "Real-time" only while ticks
 * arrive for THIS card's shown rows (never for a card none of whose rows is followed).
 */

import type { PriceAccessState } from '@/sudarshan/access'
import { istTime } from '@/sudarshan/books/time'
import { ago, type FeedLine } from '@/sudarshan/dashboard/live'
import type { Hearing } from './push'
import type { CardLineKind, EngineLine, ScreenRow, WidgetCard } from './types'

/** The most prices followed at once on the dashboard, and what a card past it says. */
export const LIVE_CAP = 100
export const NOT_RECEIVING = 'Not receiving updates on this screen · reconnecting'

export type LineIcon =
  | 'wifi-off'
  | 'circle-stop'
  | 'pause'
  | 'hourglass'
  | 'moon'
  | 'clock'
  | 'circle-check'
  | 'loader'

export interface LineView {
  icon: LineIcon
  words: string
  /** Drawn in the warning ink: the card cannot be live. */
  warn: boolean
  /** Its rows are grey: nothing on them is current. */
  greyed: boolean
}

/** Where this card's prices stand, from the dashboard's one live subscription. */
export interface CardFeed {
  /** Shown rows followed tick by tick. */
  followed: number
  /** The line over the followed rows alone; null with none. */
  line: FeedLine | null
  now: number
}

const hhmm = (at: number) => istTime(at).slice(0, 5)
const IST_MS = 19_800_000
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const
const dayOf = (at: number) => Math.floor((at + IST_MS) / 86_400_000)

/** A moment as a trader reads it: '15:30' today, 'Fri 15:30' on any other day (as the server words it). */
export function whenWords(at: number, now: number): string {
  if (dayOf(at) === dayOf(now)) return hhmm(at)
  return `${DAYS[new Date(at + IST_MS).getUTCDay()]} ${hhmm(at)}`
}

const SAID_BY_ENGINE: ReadonlySet<CardLineKind> = new Set([
  'engine',
  'feed_down',
  'waiting',
  'partial',
])

/** The card's kind and words as the state has them now (the card's own while they agree). */
function lineOf(
  card: WidgetCard,
  screen: ScreenRow | undefined,
  engine: EngineLine,
  now: number
): { kind: CardLineKind; words: string } {
  const status = screen?.status ?? card.status
  if (status === card.status && engine.state === card.line.state) {
    const { kind, words } = card.line
    // The engine's own sentence says why the card is not live (token refused, feed stopped, ...).
    return { kind, words: SAID_BY_ENGINE.has(kind) ? (card.not_live_words ?? words) : words }
  }
  const asOf = card.run ? ` · matches as of ${whenWords(card.run.at, now)}` : ''
  const reason = screen?.status_reason ?? card.status_reason
  if (status === 'stopped')
    return { kind: 'stopped', words: reason ? `Stopped · ${reason}` : 'Stopped' }
  if (status === 'paused') return { kind: 'paused', words: `Paused · not checking${asOf}` }
  const own = engine.line
  if (engine.state === 'not_answering' || engine.state === 'switched_off')
    return { kind: 'engine', words: own }
  if (engine.state === 'waiting_master') return { kind: 'waiting', words: own }
  if (card.waits) return { kind: 'waits', words: card.waits }
  // The engine's own sentence names the earliest check of any screen; this card's is its own.
  if (engine.state === 'closed') {
    const next =
      card.next_at !== null && card.next_at > now
        ? ` · next check ${whenWords(card.next_at, now)}`
        : ''
    return { kind: 'closed', words: `Market closed${next}${asOf}` }
  }
  if (engine.state === 'feed_down') return { kind: 'feed_down', words: own }
  if (card.mode === 'after_close') {
    const at = /\((\d\d:\d\d)\)/.exec(card.mode_words)?.[1]
    return {
      kind: 'after_close',
      words: `Checks after the close${at ? ` (${at})` : ''}${asOf}`,
    }
  }
  return { kind: engine.state === 'real_time' ? 'real_time' : 'partial', words: own }
}

const ICON_OF: Partial<Record<CardLineKind, LineIcon>> = {
  stopped: 'circle-stop',
  paused: 'pause',
  engine: 'wifi-off',
  feed_down: 'wifi-off',
  waiting: 'hourglass',
  waits: 'hourglass',
  closed: 'moon',
  after_close: 'clock',
}

/**
 * What a card says of its prices when they are not real-time: the app's own words for a feed
 * (LiveLine's), led by "Prices" where they would read as about something else. "Stale" only when
 * ticks are due and have stopped; a quiet minute on a working socket is only an age.
 */
function priceWords(
  line: Exclude<
    FeedLine,
    { kind: 'live' } | { kind: 'closed' } | { kind: 'idle' } | { kind: 'waiting' }
  >,
  now: number
) {
  switch (line.kind) {
    case 'delayed':
      return {
        icon: 'clock',
        warn: line.stale,
        words: `${line.stale ? 'Stale · prices updated' : 'Prices updated'} ${ago(now - line.at)}`,
      } as const
    case 'polling':
      return {
        icon: 'clock',
        warn: true,
        words: `${line.stale ? 'Stale' : 'Delayed'} · prices updated ${ago(now - line.at)}`,
      } as const
    case 'connecting':
      return {
        icon: 'wifi-off',
        warn: true,
        words: !line.again
          ? 'Connecting to prices'
          : line.at === null
            ? 'Reconnecting to prices'
            : `Reconnecting · prices updated ${ago(now - line.at)}`,
      } as const
  }
}

/** Why there are no prices to follow, when the login or the key is the cause. */
const ACCESS_LINE: Partial<Record<PriceAccessState, string>> = {
  'upstox-logged-out': 'Upstox is logged out, so there are no live prices · prices at the run',
  'no-api-key': 'No API key, so there are no live prices · prices at the run',
}

export function cardLine({
  card,
  screen,
  engine,
  hearing,
  feed,
  access = 'ready',
}: {
  card: WidgetCard
  screen: ScreenRow | undefined
  engine: EngineLine
  hearing: Hearing
  feed: CardFeed
  /** Whether prices can be read at all: the login and the key. */
  access?: PriceAccessState
}): LineView {
  if (hearing !== 'joined')
    return { icon: 'wifi-off', words: NOT_RECEIVING, warn: true, greyed: true }
  const { kind, words } = lineOf(card, screen, engine, feed.now)
  const next = screen?.next_check
    ? ` · ${screen.next_check}`
    : card.next_at
      ? ` · next check ${hhmm(card.next_at)}`
      : ''
  const icon = ICON_OF[kind]
  if (icon) {
    const down = kind === 'engine' || kind === 'feed_down'
    return {
      icon,
      words,
      warn: down || kind === 'waiting' || kind === 'waits',
      greyed: kind === 'stopped' || kind === 'paused' || down,
    }
  }
  if (kind === 'partial')
    return {
      icon: engine.state === 'warming_up' ? 'loader' : 'hourglass',
      words,
      warn: true,
      greyed: false,
    }
  // A count card shows no prices, so it never says Real-time: it says it is checking.
  if (card.kind === 'count')
    return { icon: 'circle-check', words: `Checking${next}`, warn: false, greyed: false }
  // The engine is real-time: say it of the prices only while this card's rows tick.
  if (card.rows_shown.length === 0)
    return { icon: 'clock', words: `Checking${next}`, warn: false, greyed: false }
  const unreachable = ACCESS_LINE[access]
  if (unreachable)
    return { icon: 'wifi-off', words: `${unreachable}${next}`, warn: true, greyed: false }
  if (feed.followed === 0)
    return {
      icon: 'clock',
      words: `Prices at the run · ${LIVE_CAP} prices update at most${next}`,
      warn: false,
      greyed: false,
    }
  const line = feed.line
  if (line?.kind === 'live')
    return { icon: 'circle-check', words: `Real-time${next}`, warn: false, greyed: false }
  if (line?.kind === 'closed')
    return { icon: 'moon', words: 'Market closed', warn: false, greyed: false }
  if (!line || line.kind === 'idle' || line.kind === 'waiting')
    return { icon: 'hourglass', words: `Waiting for prices${next}`, warn: true, greyed: false }
  const said = priceWords(line, feed.now)
  return {
    icon: said.icon,
    words: `${said.words}${next}`,
    warn: said.warn,
    // The rows are old only when ticks are due and stopped, or the socket is down.
    greyed: said.warn,
  }
}
