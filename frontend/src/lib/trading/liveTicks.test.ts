import { afterEach, describe, expect, it, vi } from 'vitest'
import { FrameExtrasTap, readFrameExtras } from './liveTicks'

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

  it('reads the instrument from a topic when the frame carries no names', () => {
    expect(
      readFrameExtras(JSON.stringify({ topic: 'SBIN.NSE', data: { ltp: 800, volume: 10 } }))
    ).toEqual({ symbol: 'SBIN', exchange: 'NSE', extras: { dayVolume: 10 } })
  })
})

describe('FrameExtrasTap', () => {
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
})
