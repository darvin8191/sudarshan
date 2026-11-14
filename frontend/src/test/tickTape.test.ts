import { afterEach, describe, expect, it, vi } from 'vitest'
import { feed, MarketDataManager } from './marketDataHarness'
import { buildTapes } from './tapes/scenarios'
import {
  depthFrame,
  formatTape,
  harnessSink,
  ltpFrame,
  quoteFrame,
  type Tape,
  TapePlayer,
} from './tickTape'

afterEach(() => {
  vi.useRealTimers()
  feed.reset()
})

describe('the committed tapes', () => {
  it.each(
    buildTapes().map((tape) => [tape.name, tape] as const)
  )('%s is exactly what its scenario builds', async (name, tape) => {
    // A changed scenario: run this file with -u and commit the tapes it rewrites.
    await expect(formatTape(tape)).toMatchFileSnapshot(`./tapes/${name}.json`)
    expect(JSON.parse(formatTape(tape))).toEqual(tape)
  })

  it('runs forward in time, with a scenario in words, and never a recorded session', () => {
    for (const tape of buildTapes()) {
      expect(tape.scenario.length).toBeGreaterThan(40)
      const ats = tape.entries.map((entry) => entry.at)
      expect(ats).toEqual([...ats].sort((a, b) => a - b))
      for (const entry of tape.entries) {
        if (!('frame' in entry)) continue
        // A frame is stamped by the proxy at the moment it is sent.
        expect(entry.frame.data.timestamp).toBe(tape.start + entry.at)
      }
    }
  })
})

describe('the frames', () => {
  it('speaks LTP as the proxy does: the trade keys always, the named ones only when sent', () => {
    expect(ltpFrame('NIFTY', 'NSE_INDEX', { ltp: 25_912.35, at: 1_000 })).toEqual({
      type: 'market_data',
      symbol: 'NIFTY',
      exchange: 'NSE_INDEX',
      mode: 1,
      data: { ltp: 25_912.35, ltq: 0, ltt: 0, cp: 0, timestamp: 1_000 },
    })
    const full = ltpFrame('NIFTY', 'NSE_INDEX', {
      ltp: 1,
      at: 2_000,
      tradeAt: 1_900,
      prevClose: 2,
    })
    expect(full.data).toMatchObject({ ltt: 1_900, last_trade_time: 1_900, prev_close: 2, cp: 2 })
  })

  it('carries oi, iv and greeks on a Quote only when given, and the previous close as close', () => {
    const plain = quoteFrame('SBIN', 'NSE', { ltp: 100, at: 1, prevClose: 98 })
    expect(plain.mode).toBe(2)
    expect(plain.data.close).toBe(98)
    expect(plain.data).not.toHaveProperty('oi')
    expect(plain.data).not.toHaveProperty('greeks')
    const option = quoteFrame('NIFTY27OCT2625900CE', 'NFO', {
      ltp: 140,
      at: 1,
      oi: 1_200_000,
      iv: 0.1314,
      greeks: { delta: 0.5, gamma: 0.001, theta: -9, vega: 12, rho: 0 },
    })
    expect(option.data).toMatchObject({ oi: 1_200_000, iv: 0.1314, greeks: { delta: 0.5 } })
  })

  it('puts five levels a side on a Depth frame, the best first and the rest zero', () => {
    const frame = depthFrame('SBIN', 'NSE', { ltp: 110.5, at: 1 })
    const depth = frame.data.depth as { buy: unknown[]; sell: unknown[] }
    expect(frame.mode).toBe(3)
    expect(depth.buy).toHaveLength(5)
    expect(depth.sell).toHaveLength(5)
    expect(depth.buy[0]).toEqual({ price: 110.45, quantity: 650, orders: 0 })
    expect(depth.sell[4]).toEqual({ price: 0, quantity: 0, orders: 0 })
  })
})

describe('the player', () => {
  const tape: Tape = {
    name: 'test',
    scenario: 'two frames around a hidden page and a sleep',
    start: 1_000_000,
    entries: [
      { at: 0, frame: ltpFrame('A', 'NSE', { ltp: 1, at: 1_000_000 }) },
      { at: 500, page: 'hidden' },
      { at: 500, sleep: 60_000 },
      { at: 60_500, page: 'visible' },
      { at: 61_000, frame: ltpFrame('A', 'NSE', { ltp: 2, at: 1_061_000 }) },
    ],
  }

  it('delivers in order, moves the clock between entries, and stops where it is asked', async () => {
    vi.useFakeTimers({ now: tape.start })
    const heard: string[] = []
    const player = new TapePlayer(
      tape,
      {
        frame: (f) => heard.push(`frame ${f.data.ltp} at ${Date.now() - tape.start}`),
        page: (state) => heard.push(`${state} at ${Date.now() - tape.start}`),
        sleep: (ms) => {
          vi.setSystemTime(Date.now() + ms)
        },
      },
      (ms) => vi.advanceTimersByTimeAsync(ms)
    )
    await player.to(400)
    expect(heard).toEqual(['frame 1 at 0'])
    expect(Date.now()).toBe(tape.start + 400)
    await player.end()
    expect(heard).toEqual(['frame 1 at 0', 'hidden at 500', 'visible at 60500', 'frame 2 at 61000'])
    expect(player.at).toBe(61_000)
  })

  it('lets a timer set before a sleep wait out its remaining time after it, as Android does', async () => {
    vi.useFakeTimers({ now: tape.start })
    const fired: number[] = []
    const player = new TapePlayer(
      tape,
      {
        frame: () => {},
        sleep: (ms) => {
          vi.setSystemTime(Date.now() + ms)
        },
      },
      (ms) => vi.advanceTimersByTimeAsync(ms)
    )
    await player.to(0)
    setTimeout(() => fired.push(Date.now() - tape.start), 1_000)
    await player.to(60_500)
    // 500 ms ran before the sleep; the other 500 run after it.
    expect(fired).toEqual([])
    await player.to(61_000)
    expect(fired).toEqual([61_000])
  })

  it('plays into the shared feed for a screen on the market data manager', async () => {
    vi.useFakeTimers({ now: tape.start })
    const seen: number[] = []
    MarketDataManager.getInstance().subscribe('A', 'NSE', 'LTP', (d) => seen.push(d.data.ltp ?? 0))
    const player = new TapePlayer(tape, { frame: harnessSink(feed) }, (ms) =>
      vi.advanceTimersByTimeAsync(ms)
    )
    await player.end()
    expect(seen).toEqual([1, 2])
    expect(feed.cache.get('NSE:A')?.lastUpdate).toBe(tape.start + 61_000)
  })
})
