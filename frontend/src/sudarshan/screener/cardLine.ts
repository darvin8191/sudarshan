/**
 * What a table card's line says (design 3.6, the card's line table), as one pure function. The
 * screen's status and the engine line come from the shared state cache, kept current by the state
 * push, so a card never keeps a copy of its own; the card's own words are used only while the
 * state still says what the card was read with. The first row of the table that applies wins:
 * not in the owner's room, Stopped, Paused, the feed down, waiting for history or the master,
 * the market closed, a check after the close, then the price rows: "Real-time" only while ticks
 * arrive for THIS card's shown rows (never for a card none of whose rows is followed).
 */
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
  engine: EngineLine
): { kind: CardLineKind; words: string } {
  const status = screen?.status ?? card.status
  if (status === card.status && engine.state === card.line.state) {
    const { kind, words } = card.line
    // The engine's own sentence says why the card is not live (token refused, feed stopped, ...).
    return { kind, words: SAID_BY_ENGINE.has(kind) ? (card.not_live_words ?? words) : words }
  }
  const asOf = card.run ? ` · matches as of ${hhmm(card.run.at)}` : ''
  const reason = screen?.status_reason ?? card.status_reason
  if (status === 'stopped')
    return { kind: 'stopped', words: reason ? `Stopped · ${reason}` : 'Stopped' }
  if (status === 'paused') return { kind: 'paused', words: `Paused · not checking${asOf}` }
  const own = engine.line
  if (engine.state === 'not_answering' || engine.state === 'switched_off')
    return { kind: 'engine', words: own }
  if (engine.state === 'waiting_master') return { kind: 'waiting', words: own }
  if (card.waits) return { kind: 'waits', words: card.waits }
  if (engine.state === 'closed') return { kind: 'closed', words: `${own}${asOf}` }
  if (engine.state === 'feed_down') return { kind: 'feed_down', words: own }
  if (card.mode === 'after_close')
    return { kind: 'after_close', words: `Checks after the close${asOf}` }
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

export function cardLine({
  card,
  screen,
  engine,
  hearing,
  feed,
}: {
  card: WidgetCard
  screen: ScreenRow | undefined
  engine: EngineLine
  hearing: Hearing
  feed: CardFeed
}): LineView {
  if (hearing !== 'joined')
    return { icon: 'wifi-off', words: NOT_RECEIVING, warn: true, greyed: true }
  const { kind, words } = lineOf(card, screen, engine)
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
  // The engine is real-time: say it of the prices only while this card's rows tick.
  if (card.rows_shown.length === 0)
    return { icon: 'clock', words: `Checking${next}`, warn: false, greyed: false }
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
  const at = line && 'at' in line ? line.at : null
  if (at === null || at === undefined)
    return { icon: 'hourglass', words: `Waiting for prices${next}`, warn: true, greyed: false }
  return {
    icon: 'clock',
    words: `Stale · prices updated ${ago(feed.now - at)}${next}`,
    warn: true,
    greyed: true,
  }
}
