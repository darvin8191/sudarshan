import { afterEach, describe, expect, it, vi } from 'vitest'
import { FrameExtrasTap, MAX_CLOCK_SKEW_MS, readFrameExtras, ServerClock } from './liveTicks'

const depthFrame = (data: Record<string, unknown>) =>
  JSON.stringify({
    type: 'market_data',
    symbol: 'NIFTY27OCT2625000CE',
    exchange: 'NFO',
    mode: 3,
    data: { ltp: 112.5, depth: { buy: [], sell: [] }, timestamp: 1_792_470_000_123, ...data },
  })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('readFrameExtras', () => {
  it("reads the day's volume, last quantity, open interest, previous close and trade time", () => {
    expect(
      readFrameExtras(
        depthFrame({
          volume: 1_250_000,
          last_trade_quantity: 65,
          oi: 4_100_000,
          prev_close: 108.2,
          last_trade_time: 1_792_470_000_000,
        })
      )
    ).toEqual({
      symbol: 'NIFTY27OCT2625000CE',
      exchange: 'NFO',
      extras: {
        dayVolume: 1_250_000,
        ltq: 65,
        oi: 4_100_000,
        prevClose: 108.2,
        tradeTime: 1_792_470_000,
      },
    })
  })

  it('leaves out what the frame did not carry, rather than reading it as zero', () => {
    expect(readFrameExtras(depthFrame({}))?.extras).toEqual({})
    expect(readFrameExtras(depthFrame({ volume: 'n/a', prev_close: 0 }))?.extras).toEqual({})
  })

  it('accepts numbers sent as text', () => {
    expect(readFrameExtras(depthFrame({ volume: '900', oi: '12' }))?.extras).toEqual({
      dayVolume: 900,
      oi: 12,
    })
  })

  it('ignores frames that are not market data, and text that is not a frame', () => {
    expect(readFrameExtras(JSON.stringify({ type: 'auth', status: 'success' }))).toBeNull()
    expect(
      readFrameExtras(JSON.stringify({ type: 'order_update', symbol: 'X', exchange: 'NSE' }))
    ).toBeNull()
    expect(readFrameExtras('not json')).toBeNull()
    expect(readFrameExtras(JSON.stringify({ type: 'market_data', data: { volume: 1 } }))).toBeNull()
  })

  it("reads a price-only frame's short trade time", () => {
    expect(readFrameExtras(depthFrame({ ltt: 1_792_470_005_000 }))?.extras).toEqual({
      tradeTime: 1_792_470_005,
    })
  })

  it('reads the instrument from a topic when the frame carries no names', () => {
    expect(
      readFrameExtras(JSON.stringify({ topic: 'SBIN.NSE', data: { ltp: 800, volume: 10 } }))
    ).toEqual({ symbol: 'SBIN', exchange: 'NSE', extras: { dayVolume: 10 } })
  })
})

describe('FrameExtrasTap', () => {
  it('keeps the last volume, open interest, previous close and trade time across a price-only frame, but never a last quantity', () => {
    const tap = new FrameExtrasTap()
    tap.read(
      depthFrame({
        volume: 1_250_000,
        last_trade_quantity: 65,
        oi: 4_100_000,
        prev_close: 108.2,
        last_trade_time: 1_792_470_000_000,
      })
    )
    // A price-only update reaching the book subscription: no volume, no OI.
    tap.read(
      JSON.stringify({
        type: 'market_data',
        symbol: 'NIFTY27OCT2625000CE',
        exchange: 'NFO',
        mode: 3,
        data: { ltp: 113, timestamp: 1_792_470_001_000, last_trade_time: 1_792_470_001_000 },
      })
    )
    expect(tap.get('NIFTY27OCT2625000CE', 'NFO')).toEqual({
      dayVolume: 1_250_000,
      oi: 4_100_000,
      prevClose: 108.2,
      tradeTime: 1_792_470_001,
    })
    tap.clear()
    expect(tap.get('NIFTY27OCT2625000CE', 'NFO')).toBeUndefined()
  })

  class FakeSocket {
    static last: FakeSocket | null = null
    readyState = 1
    sent: string[] = []
    onopen: (() => void) | null = null
    onclose: (() => void) | null = null
    onerror: (() => void) | null = null
    onmessage: ((event: { data: string }) => void) | null = null
    constructor(readonly url: string) {
      FakeSocket.last = this
    }
    send(data: string) {
      this.sent.push(data)
    }
    close() {
      this.readyState = 3
    }
  }

  it('reads each frame before the client sees it, through one socket', () => {
    vi.stubGlobal('WebSocket', FakeSocket)
    const tap = new FrameExtrasTap()
    const socket = tap.socketFactory('ws://test.invalid')
    const fake = FakeSocket.last as FakeSocket
    const seen: string[] = []
    socket.onmessage = (event) => {
      // What a depth callback reads, from inside the client's handler.
      seen.push(`${event.data.length}:${tap.get('NIFTY27OCT2625000CE', 'NFO')?.dayVolume}`)
    }
    const frame = depthFrame({ volume: 42_000 })
    fake.onmessage?.({ data: frame })
    expect(seen).toEqual([`${frame.length}:42000`])
    socket.send('hello')
    expect(fake.sent).toEqual(['hello'])
    expect(socket.readyState).toBe(1)
    socket.close()
    expect(socket.readyState).toBe(3)
  })

  it('forwards the lifecycle to the client', () => {
    vi.stubGlobal('WebSocket', FakeSocket)
    const tap = new FrameExtrasTap()
    const socket = tap.socketFactory('ws://test.invalid')
    const events: string[] = []
    socket.onopen = () => events.push('open')
    socket.onclose = () => events.push('close')
    socket.onerror = () => events.push('error')
    const fake = FakeSocket.last as FakeSocket
    fake.onopen?.()
    fake.onerror?.()
    fake.onclose?.()
    expect(events).toEqual(['open', 'error', 'close'])
  })

  it('answers a wake probe yes on any frame back, no on silence or with no socket open', async () => {
    vi.useFakeTimers()
    try {
      vi.stubGlobal('WebSocket', FakeSocket)
      const tap = new FrameExtrasTap()
      expect(await tap.probe()).toBe(false)
      tap.socketFactory('ws://test.invalid')
      const fake = FakeSocket.last as FakeSocket
      fake.onopen?.()
      const answered = tap.probe()
      const ping = JSON.parse(fake.sent.at(-1) as string)
      expect(ping).toMatchObject({ action: 'ping', timestamp: Date.now() })
      vi.advanceTimersByTime(200)
      fake.onmessage?.({
        data: JSON.stringify({
          type: 'pong',
          status: 'success',
          // Twenty seconds ahead, read half-way through the round trip.
          server_timestamp: ping.timestamp + 100 + 20_000,
          client_timestamp: ping.timestamp,
          _pingId: ping._pingId,
        }),
      })
      expect(await answered).toBe(true)
      expect(tap.lastFrameAt).toBe(Date.now())
      // The answer set the clock.
      expect(tap.clock.offsetMs()).toBe(20_000)
      const silent = tap.probe()
      vi.advanceTimersByTime(1_499)
      let settled = false
      void silent.then(() => {
        settled = true
      })
      await Promise.resolve()
      expect(settled).toBe(false)
      vi.advanceTimersByTime(1)
      expect(await silent).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('closes every socket at once on drop, as a dead connection would, and only once', () => {
    vi.stubGlobal('WebSocket', FakeSocket)
    const tap = new FrameExtrasTap()
    const socket = tap.socketFactory('ws://test.invalid')
    const fake = FakeSocket.last as FakeSocket
    const closed = vi.fn()
    socket.onclose = closed
    fake.onopen?.()
    tap.drop()
    // The client hears of it now, not when the browser has finished closing.
    expect(closed).toHaveBeenCalledOnce()
    expect(fake.readyState).toBe(3)
    expect(socket.readyState).toBe(3)
    // The browser's own close event, arriving later, is not a second close.
    fake.onclose?.()
    tap.drop()
    expect(closed).toHaveBeenCalledOnce()
  })
})

describe('ServerClock', () => {
  it("follows the server's clock from a ping's answer and from the frames' stamps", () => {
    let local = 1_000_000
    const clock = new ServerClock(() => local)
    // Nothing read yet: the phone's own clock.
    expect(clock.now()).toBe(1_000_000)
    // Stamped before they arrived: each is a floor under the difference, and the best of the minute counts.
    clock.noteStamp(local - 20_400, local)
    clock.noteStamp(local - 20_150, local)
    expect(clock.offsetMs()).toBe(-20_150)
    // A ping's answer: the server read its clock half-way through a 300 ms round trip.
    clock.notePong(local - 300 - 20_000 + 150, local - 300, local)
    expect(clock.offsetMs()).toBe(-20_000)
    expect(clock.nowSec()).toBe(Math.floor((1_000_000 - 20_000) / 1000))
    // A minute on, the stamps have aged out and the answer stands.
    local += 61_000
    expect(clock.offsetMs()).toBe(-20_000)
  })

  it('ignores a reading too far out to be a clock', () => {
    const local = 5_000_000
    const clock = new ServerClock(() => local)
    clock.noteStamp(local + MAX_CLOCK_SKEW_MS + 1, local)
    clock.notePong(local - 5.5 * 3_600_000, local - 100, local)
    expect(clock.offsetMs()).toBe(0)
  })

  it('reads the frames a tap sees', () => {
    const tap = new FrameExtrasTap()
    const stamp = Date.now() - 1_000
    tap.read(depthFrame({ timestamp: stamp }))
    expect(tap.clock.offsetMs()).toBeGreaterThanOrEqual(-1_050)
    expect(tap.clock.offsetMs()).toBeLessThanOrEqual(-1_000)
  })
})
