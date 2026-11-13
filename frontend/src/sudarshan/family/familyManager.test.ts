import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MarketDataManager } from '@/lib/MarketDataManager'
import { onSignedOut } from './api'
import {
  getFamilyManager,
  onFamilyBook,
  resetFamilyManagerForTests,
  setFamilyScreen,
} from './familyManager'
import { FakeSocket } from './testing/fixtures'

let fetchSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.reset()
  fetchSpy = vi.fn()
  vi.stubGlobal('fetch', fetchSpy)
  vi.stubGlobal('WebSocket', FakeSocket)
  resetFamilyManagerForTests()
})

afterEach(() => {
  resetFamilyManagerForTests()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the family market manager', () => {
  it('connects to /family/ws and asks the owner for nothing', async () => {
    const manager = getFamilyManager()
    await manager.connect()
    const socket = FakeSocket.last()
    expect(socket.url).toMatch(/^wss?:\/\/[^/]+\/family\/ws$/)
    socket.open()
    socket.say({ type: 'auth', status: 'success' })
    manager.subscribe('RELIANCE', 'NSE', 'LTP', () => undefined)
    // No config, no key, no CSRF token of the owner, no REST quotes: not one fetch.
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('never calls the owner endpoints, through a drop and a retry either', async () => {
    const manager = getFamilyManager()
    await manager.connect()
    FakeSocket.last().open()
    FakeSocket.last().close(1006)
    await vi.advanceTimersByTimeAsync(40_000)
    for (const [url] of fetchSpy.mock.calls) {
      expect(String(url)).not.toMatch(/\/api\/websocket\/apikey|\/api\/config\/host|\/auth\//)
    }
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('signs in with the family message, with no key', async () => {
    const manager = getFamilyManager()
    await manager.connect()
    const socket = FakeSocket.last()
    socket.open()
    expect(JSON.parse(socket.sent[0])).toEqual({ action: 'authenticate', family: true })
    expect(socket.sent[0]).not.toContain('api_key')
  })

  it('subscribes with last price only (mode 1), whatever mode a screen asks for', async () => {
    const manager = getFamilyManager()
    await manager.connect()
    const socket = FakeSocket.last()
    socket.open()
    socket.say({ type: 'auth', status: 'success' })
    manager.subscribe('NIFTY', 'NSE_INDEX', 'Quote', () => undefined)
    const sent = socket.sent.map((text) => JSON.parse(text))
    const subscribe = sent.find((message) => message.action === 'subscribe')
    expect(subscribe).toEqual({
      action: 'subscribe',
      symbols: [{ symbol: 'NIFTY', exchange: 'NSE_INDEX' }],
      mode: 1,
    })
  })

  it('hands ticks to subscribers', async () => {
    const manager = getFamilyManager()
    await manager.connect()
    const socket = FakeSocket.last()
    socket.open()
    socket.say({ type: 'auth', status: 'success' })
    const heard = vi.fn()
    manager.subscribe('RELIANCE', 'NSE', 'LTP', heard)
    socket.say({
      type: 'market_data',
      symbol: 'RELIANCE',
      exchange: 'NSE',
      mode: 1,
      data: { ltp: 1428.25, change: 5, change_percent: 0.4, close: 1423.25, ltt: 1, timestamp: 2 },
    })
    expect(heard).toHaveBeenLastCalledWith(
      expect.objectContaining({ data: expect.objectContaining({ ltp: 1428.25 }) })
    )
  })

  it('sends the open screen after signing in, and again after a reconnect', async () => {
    const manager = getFamilyManager()
    setFamilyScreen('positions')
    await manager.connect()
    const first = FakeSocket.last()
    first.open()
    expect(first.sent.map((text) => JSON.parse(text).action)).toEqual(['authenticate', 'screen'])
    setFamilyScreen('holdings')
    expect(JSON.parse(first.sent.at(-1) as string)).toEqual({ action: 'screen', name: 'holdings' })
    first.close(1006)
    await vi.advanceTimersByTimeAsync(1_100)
    const second = FakeSocket.last()
    expect(second).not.toBe(first)
    second.open()
    expect(JSON.parse(second.sent[1])).toEqual({ action: 'screen', name: 'holdings' })
  })

  it('tells book listeners which part was read again, at once', async () => {
    const manager = getFamilyManager()
    await manager.connect()
    const socket = FakeSocket.last()
    socket.open()
    const heard = vi.fn()
    onFamilyBook(heard)
    socket.say({ type: 'book', part: 'orders', read_at: 1234 })
    socket.say({ type: 'book', part: 'nonsense', read_at: 1 })
    expect(heard).toHaveBeenCalledTimes(1)
    expect(heard).toHaveBeenCalledWith('orders', 1234)
  })

  it('retries without limit after a drop', async () => {
    const manager = getFamilyManager()
    await manager.connect()
    for (let attempt = 0; attempt < 14; attempt++) {
      FakeSocket.last().close(1006)
      await vi.advanceTimersByTimeAsync(31_000)
    }
    expect(FakeSocket.instances.length).toBeGreaterThan(12)
  })

  it('stops, and says so, when the server closes it for a signed-out session', async () => {
    const manager = getFamilyManager()
    const heard = vi.fn()
    onSignedOut(heard)
    await manager.connect()
    FakeSocket.last().open()
    const count = FakeSocket.instances.length
    FakeSocket.last().close(4401)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(FakeSocket.instances).toHaveLength(count)
    expect(heard).toHaveBeenCalledTimes(1)
  })

  it('leaves the owner manager alone', () => {
    const owner = MarketDataManager.getInstance()
    expect(getFamilyManager()).not.toBe(owner)
    MarketDataManager.resetInstance()
  })
})
