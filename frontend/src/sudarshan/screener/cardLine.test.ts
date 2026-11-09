/**
 * The card's line (design 3.6): the first row of the table that applies, with its icon and the
 * words, and "Real-time" only while ticks arrive for the card's own shown rows.
 */
import { describe, expect, it } from 'vitest'
import { NOW, REAL_TIME, screenRow, widgetCard } from '@/test/screener'
import { type CardFeed, cardLine, LIVE_CAP, NOT_RECEIVING } from './cardLine'
import type { EngineLine, WidgetCard } from './types'

const TICKING: CardFeed = { followed: 2, line: { kind: 'live' }, now: NOW }
const NONE: CardFeed = { followed: 0, line: null, now: NOW }

function line(
  card: WidgetCard,
  over: { engine?: EngineLine; feed?: CardFeed; hearing?: 'joined' | 'not_joined' | 'unknown' } = {}
) {
  return cardLine({
    card,
    screen: screenRow({ id: card.screen_id, status: card.status }),
    engine: over.engine ?? REAL_TIME,
    hearing: over.hearing ?? 'joined',
    feed: over.feed ?? TICKING,
  })
}

const down: EngineLine = {
  state: 'feed_down',
  line: 'Not checking · Upstox refused the price feed’s token: put the current Analytics Token in the feed’s token file',
  real_time: false,
}

describe('the card line', () => {
  it('is not receiving updates until the owner’s room is joined, whatever else is true', () => {
    for (const hearing of ['unknown', 'not_joined'] as const) {
      const view = line(widgetCard(), { hearing })
      expect(view).toMatchObject({ icon: 'wifi-off', words: NOT_RECEIVING, greyed: true })
    }
  })

  it('says Real-time, with the next check, while this card’s rows tick', () => {
    const view = line(widgetCard())
    expect(view).toMatchObject({ icon: 'circle-check', words: 'Real-time · next check 10:45' })
    expect(view.greyed).toBe(false)
  })

  it('never says Real-time for a card none of whose shown rows is followed', () => {
    const view = line(widgetCard(), { feed: NONE })
    expect(view.icon).toBe('clock')
    expect(view.words).toBe(
      `Prices at the run · ${LIVE_CAP} prices update at most · next check 10:45`
    )
    expect(view.words).not.toMatch(/Real-time/)
  })

  it('says the prices are stale, with their age, when ticks stopped, and greys the rows', () => {
    const feed: CardFeed = {
      followed: 2,
      line: { kind: 'delayed', at: NOW - 12_000, stale: true },
      now: NOW,
    }
    expect(line(widgetCard(), { feed })).toMatchObject({
      icon: 'clock',
      words: 'Stale · prices updated 12s ago · next check 10:45',
      greyed: true,
      warn: true,
    })
  })

  it('says Checking, not Real-time, for a card with no rows to tick', () => {
    const view = line(widgetCard({ rows_shown: [], matches: 0 }))
    expect(view.words).toBe('Checking · next check 10:45')
  })

  it('says Stopped with its reason and Paused with the last check, both greyed', () => {
    const stopped = widgetCard({
      status: 'stopped',
      line: {
        kind: 'stopped',
        words: 'Stopped · Its list was deleted.',
        live: false,
        state: 'real_time',
      },
    })
    expect(line(stopped)).toMatchObject({
      icon: 'circle-stop',
      words: 'Stopped · Its list was deleted.',
      greyed: true,
    })
    const paused = widgetCard({
      status: 'paused',
      line: {
        kind: 'paused',
        words: 'Paused · not checking · matches as of 10:30',
        live: false,
        state: 'real_time',
      },
    })
    expect(line(paused)).toMatchObject({ icon: 'pause', greyed: true })
    expect(line(paused).words).toBe('Paused · not checking · matches as of 10:30')
  })

  it('gives the engine’s own sentence when the feed is down, so the card says why', () => {
    const card = widgetCard({
      line: { kind: 'feed_down', words: down.line, live: false, state: 'feed_down' },
      not_live_words: down.line,
    })
    const view = line(card, { engine: down })
    expect(view).toMatchObject({ icon: 'wifi-off', words: down.line, warn: true, greyed: true })
    expect(view.words).not.toMatch(/reconnecting$/)
  })

  it('follows the state cache over the card’s own copy when the state has moved on', () => {
    // Read while real-time; the state then says the feed stopped.
    const stopped: EngineLine = {
      state: 'feed_down',
      line: 'Not checking · the price feed is stopped: start sudarshan-feed again',
      real_time: false,
    }
    const view = line(widgetCard(), { engine: stopped })
    expect(view).toMatchObject({ icon: 'wifi-off', words: stopped.line })
  })

  it('waits with its own sentence, hourglass and no claim of being live', () => {
    const card = widgetCard({
      waits: 'Waits for the history download: needs 3 day candles, and today gives 1.',
      line: {
        kind: 'waits',
        words: 'Waits for the history download: needs 3 day candles, and today gives 1.',
        live: false,
        state: 'real_time',
      },
      rows_shown: [],
    })
    expect(line(card)).toMatchObject({ icon: 'hourglass', warn: true })
  })

  it('says Market closed with the last check, and a check after the close, each with an icon', () => {
    const closedEngine: EngineLine = {
      state: 'closed',
      line: 'Market closed · next check Mon 09:30',
      real_time: false,
    }
    const closed = widgetCard({
      line: {
        kind: 'closed',
        words: 'Market closed · next check Mon 09:30 · matches as of Fri 15:30',
        live: false,
        state: 'closed',
      },
    })
    expect(line(closed, { engine: closedEngine })).toMatchObject({
      icon: 'moon',
      words: 'Market closed · next check Mon 09:30 · matches as of Fri 15:30',
    })
    const after = widgetCard({
      mode: 'after_close',
      line: {
        kind: 'after_close',
        words: 'Checks after the close (16:05) · matches as of Fri 16:05',
        live: false,
        state: 'real_time',
      },
    })
    expect(line(after)).toMatchObject({
      icon: 'clock',
      words: 'Checks after the close (16:05) · matches as of Fri 16:05',
    })
  })

  it('never uses the words Live or arm', () => {
    const all = [
      line(widgetCard()).words,
      line(widgetCard(), { feed: NONE }).words,
      line(widgetCard(), { hearing: 'not_joined' }).words,
    ].join(' ')
    expect(all).not.toMatch(/\blive\b|\barm/i)
  })
})
