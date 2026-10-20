import { SessionCalendar } from 'openalgo-charts'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FeedLine } from '@/sudarshan/dashboard/live'
import {
  type LiveStatus,
  LiveStatusTracker,
  liveDetail,
  needsMarker,
  staleAfterMs,
} from './liveStatus'

const ist = (y: number, mo: number, d: number, h: number, mi: number, s = 0) =>
  Date.UTC(y, mo - 1, d, h, mi, s) - 19_800_000

const nse = () =>
  new SessionCalendar({
    timezone: 'Asia/Kolkata',
    sessions: ['0915-1530:23456'],
    exceptions: { '2026-10-21': [] },
  })

afterEach(() => {
  vi.useRealTimers()
})

describe('staleAfterMs', () => {
  it('is shortest for an index, then derivatives, then everything else', () => {
    expect(staleAfterMs('NSE_INDEX')).toBe(10_000)
    expect(staleAfterMs('BSE_INDEX')).toBe(10_000)
    expect(staleAfterMs('NFO')).toBe(15_000)
    expect(staleAfterMs('BFO')).toBe(15_000)
    expect(staleAfterMs('NSE')).toBe(30_000)
    expect(staleAfterMs('MCX')).toBe(30_000)
    expect(staleAfterMs('CDS')).toBe(30_000)
  })
})

describe('needsMarker', () => {
  it('marks every state but real-time, a first price awaited, and a fresh figure from history', () => {
    const at = 1_000
    const lines: Array<[FeedLine, boolean]> = [
      [{ kind: 'live' }, false],
      [{ kind: 'waiting' }, false],
      [{ kind: 'delayed', at, stale: false }, false],
      [{ kind: 'delayed', at, stale: true }, true],
      [{ kind: 'polling', at, stale: false }, true],
      [{ kind: 'connecting', at, stale: false, again: true }, true],
      [{ kind: 'connecting', at: null, stale: false, again: false }, true],
      [{ kind: 'closed' }, true],
    ]
    for (const [line, marked] of lines) expect(needsMarker(line)).toBe(marked)
  })
})

describe('LiveStatusTracker', () => {
  function track(exchange = 'NFO') {
    const seen: LiveStatus[] = []
    const tracker = new LiveStatusTracker({ exchange, onChange: (s) => seen.push(s) })
    const kinds = () =>
      seen.map(({ line }) =>
        line.kind === 'live' || line.kind === 'closed' || line.kind === 'waiting'
          ? line.kind
          : `${line.kind}${line.stale ? '-stale' : ''}`
      )
    return { tracker, seen, kinds }
  }

  it('is real-time while ticks arrive, stale on silence, and real-time again on the next tick', () => {
    vi.useFakeTimers()
    vi.setSystemTime(ist(2026, 10, 20, 10, 0))
    const { tracker, kinds } = track('NFO')
    // The order the terminal goes in: history loads, the hours arrive, the
    // stream signs in, the first tick lands.
    tracker.reset('NFO')
    tracker.setCalendar(nse())
    tracker.setTransport('live')
    tracker.noteTick()
    vi.advanceTimersByTime(5_000)
    tracker.noteTick()
    vi.advanceTimersByTime(14_000)
    expect(kinds()).toEqual(['connecting', 'delayed', 'live'])
    vi.advanceTimersByTime(2_000)
    expect(kinds()).toEqual(['connecting', 'delayed', 'live', 'delayed-stale'])
    tracker.noteTick()
    expect(kinds()).toEqual(['connecting', 'delayed', 'live', 'delayed-stale', 'live'])
    tracker.destroy()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('says delayed on the quote fallback, never real-time, and real-time when the stream returns', () => {
    vi.useFakeTimers()
    vi.setSystemTime(ist(2026, 10, 20, 10, 0))
    const { tracker, kinds } = track('NSE')
    tracker.setCalendar(nse())
    tracker.setTransport('live')
    tracker.noteTick()
    tracker.setTransport('connecting')
    expect(kinds().at(-1)).toBe('connecting')
    tracker.setTransport('polling')
    tracker.noteTick(undefined, false)
    // Another reconnect attempt fails while the quotes keep answering.
    tracker.setTransport('connecting')
    vi.advanceTimersByTime(4_000)
    tracker.noteTick(undefined, false)
    expect(kinds().at(-1)).toBe('polling')
    expect(tracker.status().line).toEqual({ kind: 'polling', at: Date.now(), stale: false })
    tracker.setTransport('live')
    tracker.noteTick()
    expect(kinds().at(-1)).toBe('live')
    tracker.destroy()
  })

  it('turns stale when the quotes stop answering too', () => {
    vi.useFakeTimers()
    vi.setSystemTime(ist(2026, 10, 20, 10, 0))
    const { tracker, kinds } = track('NFO')
    tracker.setCalendar(nse())
    tracker.setTransport('polling')
    tracker.noteTick(undefined, false)
    vi.advanceTimersByTime(16_000)
    expect(kinds().at(-1)).toBe('polling-stale')
    tracker.destroy()
  })

  it('turns to closed at the session close and gives the next open', () => {
    vi.useFakeTimers()
    vi.setSystemTime(ist(2026, 10, 20, 15, 29, 55))
    const { tracker, seen } = track('NSE_INDEX')
    tracker.setCalendar(nse())
    tracker.setTransport('live')
    tracker.noteTick()
    vi.advanceTimersByTime(6_000)
    const last = seen.at(-1) as LiveStatus
    expect(last.line).toEqual({ kind: 'closed' })
    // Wednesday is a holiday, so the next open is Thursday's.
    expect(last.opensAt).toBe(ist(2026, 10, 22, 9, 15) / 1000)
    expect(liveDetail(last, Date.now())).toBe('Opens Thu 09:15. Prices are from the last session.')
    tracker.destroy()
  })

  it('opens again on time without a tick, and goes real-time on the first one', () => {
    vi.useFakeTimers()
    vi.setSystemTime(ist(2026, 10, 22, 9, 14, 50))
    const { tracker, kinds } = track('NFO')
    tracker.setCalendar(nse())
    tracker.setTransport('live')
    tracker.reset('NFO')
    expect(kinds()).toEqual(['closed'])
    vi.advanceTimersByTime(11_000)
    expect(kinds()).toEqual(['closed', 'delayed'])
    tracker.noteTick()
    expect(kinds()).toEqual(['closed', 'delayed', 'live'])
    tracker.destroy()
  })

  it('starts over for a new instrument, with its own threshold', () => {
    vi.useFakeTimers()
    vi.setSystemTime(ist(2026, 10, 20, 10, 0))
    const { tracker } = track('NFO')
    tracker.setTransport('live')
    tracker.noteTick(1_792_470_000)
    expect(tracker.status().lastTickTime).toBe(1_792_470_000)
    tracker.reset('NSE_INDEX')
    expect(tracker.status()).toMatchObject({
      line: { kind: 'delayed', at: Date.now(), stale: false },
      lastTickAt: null,
      lastTickTime: null,
      staleAfterMs: 10_000,
    })
    tracker.destroy()
  })
})

describe('the sentence behind a marker', () => {
  const now = ist(2026, 10, 20, 10, 0)
  const status = (line: FeedLine): LiveStatus => ({
    line,
    lastTickAt: null,
    lastUpdateAt: null,
    lastTickTime: null,
    staleAfterMs: 15_000,
    opensAt: null,
  })

  it('names a time rather than an age, so it stays true while it is shown', () => {
    expect(liveDetail(status({ kind: 'delayed', at: now - 25_000, stale: true }), now)).toBe(
      'No price update since 09:59:35. The last price shown may be out of date.'
    )
    expect(
      liveDetail(status({ kind: 'connecting', at: now - 6_000, stale: false, again: true }), now)
    ).toBe('The real-time feed dropped and is reconnecting. Prices shown are from 09:59:54.')
  })

  it('never says Live for the feed, nor a protocol word, nor a retired one', () => {
    const lines: FeedLine[] = [
      { kind: 'live' },
      { kind: 'waiting' },
      { kind: 'delayed', at: now, stale: false },
      { kind: 'delayed', at: now, stale: true },
      { kind: 'polling', at: now, stale: false },
      { kind: 'polling', at: now, stale: true },
      { kind: 'connecting', at: null, stale: false, again: false },
      { kind: 'connecting', at: now, stale: true, again: true },
      { kind: 'closed' },
    ]
    const words = lines.map((line) => liveDetail(status(line), now)).join(' ')
    expect(words).not.toMatch(/\blive\b/i)
    expect(words).not.toMatch(/\b(websocket|socket|http|api|paper|arm|armed|arming)\b/i)
  })
})
