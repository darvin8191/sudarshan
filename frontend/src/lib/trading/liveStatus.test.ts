import { SessionCalendar } from 'openalgo-charts'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  LIVE_LABEL,
  type LiveStatus,
  LiveStatusTracker,
  liveDetail,
  liveStateOf,
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

describe('liveStateOf', () => {
  const base = {
    transport: 'live' as const,
    marketOpen: true,
    lastTickAt: 100_000,
    since: 0,
    now: 105_000,
    staleAfterMs: 10_000,
  }

  it('is live while ticks arrive inside the session', () => {
    expect(liveStateOf(base)).toBe('live')
  })

  it('is stale when nothing has arrived for longer than the threshold', () => {
    expect(liveStateOf({ ...base, now: 111_000 })).toBe('stale')
  })

  it('is delayed while prices come from the quote fallback', () => {
    expect(liveStateOf({ ...base, transport: 'polling' })).toBe('delayed')
    expect(liveStateOf({ ...base, transport: 'polling', now: 120_000 })).toBe('stale')
  })

  it('says the market is closed before anything else', () => {
    expect(liveStateOf({ ...base, marketOpen: false })).toBe('closed')
    expect(liveStateOf({ ...base, marketOpen: false, transport: 'polling' })).toBe('closed')
  })

  it('judges by ticks alone when the hours are not known', () => {
    expect(liveStateOf({ ...base, marketOpen: null })).toBe('live')
    expect(liveStateOf({ ...base, marketOpen: null, now: 200_000 })).toBe('stale')
  })

  it('waits for the first tick, then calls the silence stale', () => {
    expect(liveStateOf({ ...base, lastTickAt: null, since: 100_000 })).toBe('connecting')
    expect(liveStateOf({ ...base, lastTickAt: null, since: 90_000 })).toBe('stale')
  })
})

describe('LiveStatusTracker', () => {
  function track(exchange = 'NFO') {
    const seen: LiveStatus[] = []
    const tracker = new LiveStatusTracker({ exchange, onChange: (s) => seen.push(s) })
    return { tracker, seen, states: () => seen.map((s) => s.state) }
  }

  it('goes live on the first tick, stale on silence, and live again on the next tick', () => {
    vi.useFakeTimers()
    vi.setSystemTime(ist(2026, 10, 20, 10, 0))
    const { tracker, states } = track('NFO')
    tracker.setCalendar(nse())
    tracker.setTransport('live')
    tracker.noteTick()
    vi.advanceTimersByTime(5_000)
    tracker.noteTick()
    vi.advanceTimersByTime(14_000)
    expect(states()).toEqual(['connecting', 'live'])
    vi.advanceTimersByTime(2_000)
    expect(states()).toEqual(['connecting', 'live', 'stale'])
    tracker.noteTick()
    expect(states()).toEqual(['connecting', 'live', 'stale', 'live'])
    tracker.destroy()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('says delayed on the fallback and live again when the stream returns', () => {
    vi.useFakeTimers()
    vi.setSystemTime(ist(2026, 10, 20, 10, 0))
    const { tracker, states } = track('NSE')
    tracker.setCalendar(nse())
    tracker.setTransport('live')
    tracker.noteTick()
    tracker.setTransport('polling')
    tracker.noteTick()
    // Another reconnect attempt fails while the quotes keep answering.
    tracker.setTransport('connecting')
    tracker.noteTick()
    tracker.setTransport('live')
    tracker.noteTick()
    expect(states()).toEqual(['connecting', 'live', 'delayed', 'live'])
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
    const last = seen.at(-1)
    expect(last?.state).toBe('closed')
    // Wednesday is a holiday, so the next open is Thursday's.
    expect(last?.opensAt).toBe(ist(2026, 10, 22, 9, 15) / 1000)
    expect(liveDetail(last as LiveStatus, Date.now())).toBe(
      'Opens Thu 09:15. Prices are from the last session.'
    )
    tracker.destroy()
  })

  it('opens again on time without a tick', () => {
    vi.useFakeTimers()
    vi.setSystemTime(ist(2026, 10, 22, 9, 14, 50))
    const { tracker, states } = track('NFO')
    tracker.setCalendar(nse())
    tracker.setTransport('live')
    expect(states()).toEqual(['closed'])
    vi.advanceTimersByTime(11_000)
    expect(states()).toEqual(['closed', 'connecting'])
    tracker.noteTick()
    expect(states()).toEqual(['closed', 'connecting', 'live'])
    tracker.destroy()
  })

  it('starts over for a new instrument', () => {
    vi.useFakeTimers()
    vi.setSystemTime(ist(2026, 10, 20, 10, 0))
    const { tracker, seen } = track('NFO')
    tracker.setTransport('live')
    tracker.noteTick(1_792_470_000)
    expect(seen.at(-1)?.lastTickTime).toBe(1_792_470_000)
    tracker.reset('NSE_INDEX')
    expect(tracker.status()).toMatchObject({
      state: 'connecting',
      lastTickAt: null,
      lastTickTime: null,
      staleAfterMs: 10_000,
    })
    tracker.destroy()
  })
})

describe('the words a trader reads', () => {
  it('labels every state and never names a protocol or a word the platform retired', () => {
    expect(LIVE_LABEL).toEqual({
      live: 'Live',
      delayed: 'Delayed',
      stale: 'Stale',
      closed: 'Market closed',
      connecting: 'Connecting',
    })
    const now = ist(2026, 10, 20, 10, 0)
    const status = (state: LiveStatus['state']): LiveStatus => ({
      state,
      lastTickAt: now - 25_000,
      lastTickTime: null,
      staleAfterMs: 15_000,
      opensAt: null,
    })
    const words = (['live', 'delayed', 'stale', 'closed', 'connecting'] as const)
      .map((state) => liveDetail(status(state), now))
      .join(' ')
    expect(liveDetail(status('stale'), now)).toBe(
      'No price update since 09:59:35. The last price shown may be out of date.'
    )
    expect(words).not.toMatch(/\b(websocket|socket|http|api|paper|arm|armed|arming)\b/i)
  })
})
