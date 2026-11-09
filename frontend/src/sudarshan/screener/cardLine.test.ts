/**
 * The card's line (design 3.6): the first row of the table that applies, with its icon and the
 * words, and "Real-time" only while ticks arrive for the card's own shown rows.
 */
import { describe, expect, it } from 'vitest'
import { feedLine } from '@/sudarshan/dashboard/live'
import { statusLine } from '@/sudarshan/watchlist/rows'
import { NOW, REAL_TIME, screenRow, widgetCard } from '@/test/screener'
import { type CardFeed, cardLine, LIVE_CAP, NOT_RECEIVING, whenWords } from './cardLine'
import type { EngineLine, WidgetCard } from './types'

const TICKING: CardFeed = { followed: 2, line: { kind: 'live' }, now: NOW }
const NONE: CardFeed = { followed: 0, line: null, now: NOW }

function line(
  card: WidgetCard,
  over: {
    engine?: EngineLine
    feed?: CardFeed
    hearing?: 'joined' | 'not_joined' | 'unknown'
    access?: 'ready' | 'checking' | 'upstox-logged-out' | 'no-api-key'
  } = {}
) {
  return cardLine({
    access: over.access,
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

describe('the card line, in the app’s words for the feed, the login and the close (M9.16)', () => {
  it('a quiet minute on a working socket is an age, not Stale, and the rows stay black', () => {
    const quiet = statusLine({
      now: NOW,
      marketOpen: true,
      lastTickAt: NOW - 15_000,
      lastUpdateAt: NOW - 15_000,
      connected: true,
      fallback: false,
    })
    const view = line(widgetCard(), { feed: { followed: 3, line: quiet, now: NOW } })
    expect(view).toMatchObject({
      words: 'Prices updated 15s ago · next check 10:45',
      warn: false,
      greyed: false,
    })
    expect(view.words).not.toMatch(/Stale/)
  })

  it('says Reconnecting while the market-data socket is down, with the age of the last price', () => {
    const down = feedLine({
      now: NOW,
      marketOpen: true,
      lastTickAt: NOW - 3_000,
      lastUpdateAt: NOW - 3_000,
      ticksDue: true,
      connected: false,
    })
    const view = line(widgetCard(), { feed: { followed: 3, line: down, now: NOW } })
    expect(view.words).toBe('Reconnecting · prices updated 3s ago · next check 10:45')
    expect(view).toMatchObject({ icon: 'wifi-off', warn: true, greyed: true })
    const first = feedLine({
      now: NOW,
      marketOpen: true,
      lastTickAt: null,
      lastUpdateAt: null,
      ticksDue: true,
      connected: false,
    })
    expect(line(widgetCard(), { feed: { followed: 3, line: first, now: NOW } }).words).toMatch(
      /^Connecting to prices/
    )
  })

  it('says Delayed while a quote request fills in for a down socket, Stale once it is old', () => {
    const polling = (stale: boolean): CardFeed => ({
      followed: 2,
      line: { kind: 'polling', at: NOW - 6_000, stale },
      now: NOW,
    })
    expect(line(widgetCard(), { feed: polling(false) }).words).toMatch(
      /^Delayed · prices updated 6s ago/
    )
    expect(line(widgetCard(), { feed: polling(true) }).words).toMatch(
      /^Stale · prices updated 6s ago/
    )
  })

  it('says why when Upstox is logged out or there is no key, and never Waiting for prices', () => {
    const none: CardFeed = { followed: 2, line: { kind: 'idle' }, now: NOW }
    const out = line(widgetCard(), { feed: none, access: 'upstox-logged-out' })
    expect(out.words).toMatch(
      /^Upstox is logged out, so there are no live prices · prices at the run/
    )
    expect(out).toMatchObject({ icon: 'wifi-off', warn: true })
    expect(line(widgetCard(), { feed: none, access: 'no-api-key' }).words).toMatch(/No API key/)
    expect(line(widgetCard(), { feed: none, access: 'checking' }).words).toMatch(
      /^Waiting for prices/
    )
  })

  it('after the close, a card names its own next check with the day, not the engine’s earliest', () => {
    const closed: EngineLine = {
      state: 'closed',
      line: 'Market closed · next check 16:05',
      real_time: false,
      next_at: NOW + 3_600_000,
    }
    const tuesdayOpen = Date.UTC(2026, 10, 10, 4, 0, 0)
    const view = line(widgetCard({ next_at: tuesdayOpen }), {
      engine: closed,
      feed: { followed: 2, line: { kind: 'closed' }, now: NOW },
    })
    expect(view.words).toBe('Market closed · next check Tue 09:30 · matches as of 10:45')
    expect(view.words).not.toMatch(/16:05/)
  })

  it('names the day of an earlier check, and the time of a check after the close', () => {
    expect(whenWords(NOW, NOW)).toBe('10:45')
    expect(whenWords(NOW - 86_400_000, NOW)).toBe('Sun 10:45')
    const after = widgetCard({
      mode: 'after_close',
      mode_words: 'After the close (16:05)',
      line: { kind: 'after_close', words: 'x', live: false, state: 'closed' },
    })
    expect(line(after).words).toBe('Checks after the close (16:05) · matches as of 10:45')
  })
})
