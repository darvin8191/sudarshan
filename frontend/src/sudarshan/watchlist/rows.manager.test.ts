/**
 * A row's figures through the real market-data manager (M8.2): a frame goes in
 * as the socket delivers it, and the row is worked out from what the manager
 * kept of it. The other watchlist tests use the manager's test harness, which
 * merges every field; this one holds the row to the manager's own merge.
 *
 * Until the completed Upstox payloads (M11.3, `feat/feed-complete`) are in,
 * the manager drops `prev_close` from a frame, and the change is measured from
 * the snapshot's previous close. Once it keeps it, a tick's wins. Exactly one
 * of the two prev_close cases below runs, whichever the manager in this tree
 * calls for.
 */
import { afterEach, describe, expect, it } from 'vitest'
import type { QuotesData } from '@/api/trading'
import { MarketDataManager } from '@/lib/MarketDataManager'
import { rowView, type Snapshot } from './rows'

interface Inbox {
  handleMessage: (event: MessageEvent) => void
}

function deliver(manager: MarketDataManager, symbol: string, data: Record<string, unknown>) {
  ;(manager as unknown as Inbox).handleMessage(
    new MessageEvent('message', {
      data: JSON.stringify({ type: 'market_data', symbol, exchange: 'NSE', data }),
    })
  )
}

/** Whether this tree's manager keeps a frame's prev_close. */
const keepsPrevClose = (() => {
  const manager = MarketDataManager.getInstance()
  const release = manager.subscribe('PROBE', 'NSE', 'LTP', () => {})
  deliver(manager, 'PROBE', { ltp: 10, prev_close: 9 })
  const kept = (manager.getCachedData('PROBE', 'NSE')?.data as { prev_close?: number })?.prev_close
  release()
  MarketDataManager.resetInstance()
  return kept === 9
})()

const SNAPSHOT: Snapshot = {
  quote: {
    ltp: 812.4,
    prev_close: 800,
    open: 0,
    high: 0,
    low: 0,
    volume: 0,
    oi: 0,
    bid: 0,
    ask: 0,
  } as QuotesData,
  // Older than any frame below, so the frames' level wins.
  at: Date.now() - 60_000,
}

function streaming() {
  const manager = MarketDataManager.getInstance()
  const release = manager.subscribe('SBIN', 'NSE', 'LTP', () => {})
  return {
    send: (data: Record<string, unknown>) => deliver(manager, 'SBIN', data),
    row: () => rowView(SNAPSHOT, manager.getCachedData('SBIN', 'NSE')),
    release,
  }
}

afterEach(() => {
  MarketDataManager.resetInstance()
})

describe('a row through the real manager', () => {
  it('keeps the level and the book a later frame leaves out', () => {
    const feed = streaming()
    feed.send({ ltp: 820, depth: { buy: [{ price: 819.95, quantity: 10 }], sell: [] } })
    feed.send({ ltp: 821 })
    expect(feed.row()).toMatchObject({ ltp: 821, live: true })
    feed.send({ depth: { buy: [{ price: 820.95, quantity: 5 }], sell: [] } })
    // A frame with no level does not clear it.
    expect(feed.row().ltp).toBe(821)
    feed.release()
  })

  it.runIf(keepsPrevClose)(
    "measures from a tick's prev_close, and a frame without one keeps it",
    () => {
      const feed = streaming()
      feed.send({ ltp: 820, prev_close: 805 })
      expect(feed.row()).toMatchObject({ ltp: 820, prevClose: 805 })
      expect(feed.row().change).toBeCloseTo(15)

      feed.send({ ltp: 821 })
      expect(feed.row().prevClose).toBe(805)

      // A zero the broker sends for an unknown close is not a previous close.
      feed.send({ ltp: 822, prev_close: 0 })
      expect(feed.row().prevClose).toBe(800)
      feed.release()
    }
  )

  it.skipIf(keepsPrevClose)(
    "measures from the snapshot's previous close while the manager drops a tick's",
    () => {
      const feed = streaming()
      feed.send({ ltp: 820, prev_close: 805 })
      expect(feed.row()).toMatchObject({ ltp: 820, prevClose: 800 })
      expect(feed.row().change).toBeCloseTo(20)
      feed.release()
    }
  )
})
