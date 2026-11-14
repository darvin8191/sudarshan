/**
 * The scenarios behind the committed tick tapes (M4.1, C13), each stated in
 * words and built here in the proxy's message shape. Every price, quantity
 * and time is invented for the scenario; nothing is recorded from a session.
 *
 * The day is Tuesday 20 October 2026, a trading day, so the exchange's hours
 * are the ordinary 09:15 to 15:30.
 */
import { depthFrame, type Tape, type TapeEntry } from '../tickTape'

/** Epoch milliseconds for a time of day in India on Tuesday 20 October 2026. */
export const ist = (h: number, m: number, s = 0) =>
  Date.UTC(2026, 9, 20, h, m, 0) + Math.round(s * 1000) - 19_800_000

function tape(name: string, scenario: string, start: number, entries: TapeEntry[]): Tape {
  return { name, scenario, start, entries: entries.map((e) => ({ ...e, at: e.at - start })) }
}

const SBIN = ['SBIN', 'NSE'] as const
const OPTION = ['NIFTY27OCT2625900CE', 'NFO'] as const

/** The five-minute boundary at 10:05 passes with no trade; the first trade comes 20 s later. */
function boundaryNoTrade(): Tape {
  const frame = (at: number, tradeAt: number, ltp: number, volume: number, ltq: number) => ({
    at,
    frame: depthFrame(...SBIN, { ltp, at, tradeAt, volume, ltq, prevClose: 98.5 }),
  })
  return tape(
    'boundary-no-trade-5m',
    'SBIN on 5m: trades until 10:04:58, nothing at all across 10:05:00, the first trade of ' +
      'the 10:05 bar at 10:05:19.8. The 10:05 candle opens on time, flat at the last price, ' +
      'and the first trade replaces it.',
    ist(10, 4, 30),
    [
      frame(ist(10, 4, 31), ist(10, 4, 31), 110.5, 50_000, 25),
      frame(ist(10, 4, 45), ist(10, 4, 44.6), 110.55, 50_200, 200),
      frame(ist(10, 4, 58), ist(10, 4, 57.9), 110.6, 50_350, 150),
      frame(ist(10, 5, 20), ist(10, 5, 19.8), 110.4, 50_500, 150),
      frame(ist(10, 5, 40), ist(10, 5, 39.5), 110.7, 50_800, 300),
    ]
  )
}

/** The last trade of 10:00-10:05, at 10:04:59.7, reaches the phone at 10:05:00.4. */
function lateTrade(): Tape {
  const frame = (at: number, ltp: number, tradeAt: number, volume: number) => ({
    at,
    frame: depthFrame(...SBIN, { ltp, at, tradeAt, volume, prevClose: 98.5 }),
  })
  return tape(
    'late-trade-5m',
    'SBIN on 5m: a trade at 10:04:59.7 arrives at 10:05:00.4, after the clock opened 10:05; ' +
      'then the book moves with no trade; then the first trade of 10:05. The late trade goes to ' +
      'the 10:00 candle and the first trade of 10:05 is its open.',
    ist(10, 4, 50),
    [
      frame(ist(10, 4, 51), 110.5, ist(10, 4, 51), 50_000),
      frame(ist(10, 5, 0.4), 110.8, ist(10, 4, 59.7), 50_300),
      frame(ist(10, 5, 0.6), 110.8, ist(10, 4, 59.7), 50_300),
      frame(ist(10, 5, 1.1), 111, ist(10, 5, 1), 50_450),
    ]
  )
}

/** A phone asleep for twelve minutes on a one-minute chart of an option. */
function wakeGap(): Tape {
  const greeks = { delta: 0.52, gamma: 0.0011, theta: -9.8, vega: 12.4, rho: 0 }
  const frame = (at: number, ltp: number, volume: number, oi: number) => ({
    at,
    frame: depthFrame(...OPTION, {
      ltp,
      at,
      tradeAt: at - 200,
      volume,
      ltq: 65,
      prevClose: 131.2,
      oi,
      iv: 0.1314,
      greeks,
    }),
  })
  const entries: TapeEntry[] = []
  for (let i = 0; i < 5; i++)
    entries.push(
      frame(ist(10, 0, 5 + i * 10), 140 + i * 0.25, 50_000 + i * 650, 1_200_000 + i * 650)
    )
  entries.push({ at: ist(10, 0, 46), page: 'hidden' })
  entries.push({ at: ist(10, 0, 46), sleep: 12 * 60_000 })
  entries.push({ at: ist(10, 12, 46), page: 'visible' })
  // The page's socket died in the sleep; these reach the socket opened after the wake.
  entries.push(frame(ist(10, 12, 49), 142.3, 61_000, 1_206_500))
  entries.push(frame(ist(10, 12, 55), 142.35, 61_650, 1_207_150))
  entries.push(frame(ist(10, 13, 0.5), 142.2, 62_300, 1_207_800))
  return tape(
    'wake-gap-1m',
    'An option on 1m: ticks until 10:00:45, then the phone sleeps from 10:00:46 to 10:12:46 ' +
      'and its socket dies. On the wake the chart says Reconnecting at once, reads the gap from ' +
      'its last bar to now, replaces the dead socket, and the first frame on the new one is the ' +
      "broker's current state, at 10:12:49.",
    ist(10, 0, 0),
    entries
  )
}

/** The socket stops answering while the page is away for ten seconds. */
function wakeSilent(): Tape {
  const frame = (at: number, ltp: number, volume: number) => ({
    at,
    frame: depthFrame(...SBIN, { ltp, at, tradeAt: at - 100, volume, prevClose: 98.5 }),
  })
  return tape(
    'wake-silent-5m',
    'SBIN on 5m: two ticks, the page hidden for ten seconds, and the socket silent after it. ' +
      'The wake probe gets no answer and the socket is replaced within a second and a half.',
    ist(10, 0, 28),
    [
      frame(ist(10, 0, 30), 110.5, 50_000),
      frame(ist(10, 0, 35), 110.55, 50_200),
      { at: ist(10, 0, 36), page: 'hidden' },
      { at: ist(10, 0, 46), page: 'visible' },
    ]
  )
}

/** A phone asleep across thirteen minutes, and then a minute's boundary nobody trades on. */
function sleepBoundary(): Tape {
  const frame = (at: number, tradeAt: number, ltp: number, volume: number) => ({
    at,
    frame: depthFrame(...SBIN, { ltp, at, tradeAt, volume, prevClose: 98.5 }),
  })
  return tape(
    'sleep-boundary-1m',
    'SBIN on 1m: a trade at 10:00:01, then the phone sleeps from 10:00:02 to 10:13:30 with its ' +
      'socket alive. One trade at 10:13:30.8 after the wake, nothing at all across 10:14:00, and ' +
      'the first trade of the 10:14 bar at 10:14:39.8. The 10:14 candle opens at 10:14:00, flat, ' +
      'though a timer set before the sleep still had most of a minute to run.',
    ist(10, 0, 0),
    [
      frame(ist(10, 0, 1), ist(10, 0, 1), 120.5, 9_000),
      { at: ist(10, 0, 2), page: 'hidden' },
      { at: ist(10, 0, 2), sleep: 13 * 60_000 + 28_000 },
      { at: ist(10, 13, 30), page: 'visible' },
      frame(ist(10, 13, 31), ist(10, 13, 30.8), 120.6, 9_500),
      frame(ist(10, 14, 40), ist(10, 14, 39.8), 120.7, 9_650),
    ]
  )
}

/** Every committed tape, by name. */
export function buildTapes(): Tape[] {
  return [boundaryNoTrade(), lateTrade(), wakeGap(), wakeSilent(), sleepBoundary()]
}
