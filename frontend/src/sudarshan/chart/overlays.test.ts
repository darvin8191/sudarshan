import { describe, expect, it } from 'vitest'
import type { AlertRow } from '@/api/marketAlerts'
import type { Order, Position } from '@/types/trading'
import {
  alertLine,
  clusterLabels,
  LABEL_TARGET,
  linesOf,
  MAX_LINES,
  orderLines,
  positionLabel,
} from './overlays'

const SYMBOL = 'NIFTY18NOV2625900CE'
const position = (over: Partial<Position> = {}): Position => ({
  symbol: SYMBOL,
  exchange: 'NFO',
  product: 'MIS',
  quantity: 65,
  average_price: 131.2,
  ltp: 142.35,
  pnl: 724.75,
  pnlpercent: 8.5,
  ...over,
})
const order = (over: Partial<Order> = {}): Order => ({
  symbol: SYMBOL,
  exchange: 'NFO',
  action: 'SELL',
  quantity: 65,
  price: 0,
  trigger_price: 120,
  pricetype: 'SL-M',
  product: 'MIS',
  orderid: 'A1',
  order_status: 'trigger pending',
  timestamp: '2026-11-16 10:41:51',
  ...over,
})
const alert = (over: Partial<AlertRow> = {}): AlertRow =>
  ({
    id: 7,
    version: 1,
    status: 'active',
    kind: 'price',
    symbol: SYMBOL,
    exchange: 'NFO',
    params: { op: 'at_or_above', level: '150.00' },
    group_id: null,
    ...over,
  }) as AlertRow

const books = (over: Partial<Parameters<typeof linesOf>[0]> = {}) =>
  linesOf({
    symbol: SYMBOL,
    exchange: 'NFO',
    decimals: 2,
    positions: [],
    orders: [],
    alerts: [],
    ...over,
  })

describe('the lines of the charted instrument', () => {
  it('writes a position as its side, size and average, a long as Buy and a short as Sell', () => {
    expect(positionLabel(position(), 2)).toBe('Buy 65 @ 131.20')
    expect(positionLabel(position({ quantity: -130, average_price: 88.05 }), 2)).toBe(
      'Sell 130 @ 88.05'
    )
    const { lines } = books({ positions: [position()] })
    expect(lines).toEqual([
      expect.objectContaining({
        kind: 'position',
        side: 'BUY',
        price: 131.2,
        label: 'Buy 65 @ 131.20',
      }),
    ])
  })

  it('names the product when the instrument is held in two', () => {
    const { lines } = books({
      positions: [position(), position({ product: 'NRML', quantity: -65 })],
    })
    expect(lines.map((line) => [line.id, line.label, line.side])).toEqual([
      ['position:MIS', 'Buy 65 @ 131.20 · MIS', 'BUY'],
      ['position:NRML', 'Sell 65 @ 131.20 · NRML', 'SELL'],
    ])
  })

  it('draws nothing for a closed position, another instrument or another exchange', () => {
    const { lines } = books({
      positions: [
        position({ quantity: 0 }),
        position({ symbol: 'NIFTY18NOV2625900PE' }),
        position({ exchange: 'BFO' }),
      ],
    })
    expect(lines).toEqual([])
  })

  it('writes an order with the word of its side, its size, type and price', () => {
    expect(orderLines(order(), 2).map((line) => [line.label, line.price, line.side])).toEqual([
      ['SELL 65 SL-M 120.00', 120, 'SELL'],
    ])
    expect(
      orderLines(order({ action: 'BUY', pricetype: 'LIMIT', price: 128.5, trigger_price: 0 }), 2)[0]
        .label
    ).toBe('BUY 65 LIMIT 128.50')
  })

  it('draws a stop-limit order as two lines, its trigger and its limit, each saying which', () => {
    const lines = orderLines(order({ pricetype: 'SL', price: 119.5 }), 2)
    expect(lines.map((line) => [line.id, line.label])).toEqual([
      ['order:A1:trigger', 'SELL 65 SL trigger 120.00'],
      ['order:A1:price', 'SELL 65 SL limit 119.50'],
    ])
  })

  it('draws what is left to fill, and no line for a market order or one that has ended', () => {
    const part = { ...order({ pricetype: 'LIMIT', price: 130 }), pending_quantity: 20 } as Order
    expect(orderLines(part, 2)[0].label).toBe('SELL 20 LIMIT 130.00')
    expect(orderLines(order({ pricetype: 'MARKET', trigger_price: 0 }), 2)).toEqual([])
    for (const order_status of ['complete', 'cancelled', 'rejected'] as const)
      expect(books({ orders: [order({ order_status })] }).lines).toEqual([])
  })

  it('draws an active price alert with its condition in words, and no other alert', () => {
    expect(alertLine(alert(), 2)).toMatchObject({
      id: 'alert:7',
      side: null,
      price: 150,
      label: 'Alert ≥ 150.00',
    })
    expect(
      alertLine(alert({ params: { op: 'crosses_below', level: '99.5' } } as never), 2)?.label
    ).toBe('Alert crosses below 99.50')
    expect(alertLine(alert({ status: 'paused' }), 2)).toBeNull()
    expect(alertLine(alert({ kind: 'vwap' }), 2)).toBeNull()
    expect(alertLine(alert({ group_id: 3 }), 2)).toBeNull()
  })

  it('puts the position first, then orders, then alerts', () => {
    const { lines } = books({
      positions: [position()],
      orders: [order()],
      alerts: [alert()],
    })
    expect(lines.map((line) => line.kind)).toEqual(['position', 'order', 'alert'])
  })

  it('draws twelve lines at most and says how many orders and alerts were left out', () => {
    const many = Array.from({ length: 13 }, (_, i) =>
      order({ orderid: `A${i}`, pricetype: 'LIMIT', price: 100 + i, trigger_price: 0 })
    )
    const capped = books({ positions: [position()], orders: many, alerts: [alert()] })
    expect(capped.lines).toHaveLength(MAX_LINES)
    expect(capped.lines.filter((line) => line.kind === 'order')).toHaveLength(11)
    expect(capped.more).toBe('2 more orders · see Orders · 1 more alert · see Alerts')
    expect(books({ orders: many.slice(0, 12) }).more).toBeNull()
    expect(books({ orders: many }).more).toBe('1 more order · see Orders')
  })

  it('marks the lines of a book that could not be read grey with when it was read, alerts apart', () => {
    const { lines } = books({
      positions: [position()],
      orders: [order()],
      alerts: [alert()],
      staleNote: 'as of 10:42:07',
    })
    expect(lines.map((line) => [line.kind, line.stale ?? false, line.note ?? null])).toEqual([
      ['position', true, 'as of 10:42:07'],
      ['order', true, 'as of 10:42:07'],
      ['alert', false, null],
    ])
  })
})

describe('where the labels can be touched', () => {
  it('gives a line alone a 48 px target centred on it', () => {
    expect(clusterLabels([{ id: 'a', y: 200 }])).toEqual([
      { ids: ['a'], top: 176, height: LABEL_TARGET, shown: [{ id: 'a', y: 200, extra: 0 }] },
    ])
  })

  it('keeps lines a target apart in targets of their own, top to bottom', () => {
    const clusters = clusterLabels([
      { id: 'low', y: 300 },
      { id: 'high', y: 100 },
    ])
    expect(clusters.map((c) => c.ids)).toEqual([['high'], ['low']])
  })

  it('shares one target between lines nearer than 48 px, never over the next', () => {
    const clusters = clusterLabels([
      { id: 'a', y: 100 },
      { id: 'b', y: 130 },
      { id: 'c', y: 150 },
    ])
    expect(clusters.map((c) => c.ids)).toEqual([['a', 'b'], ['c']])
    const [first, second] = clusters
    expect(first.top).toBe(76)
    expect(first.top + first.height).toBeLessThanOrEqual(second.top)
    expect(first.height).toBeGreaterThanOrEqual(LABEL_TARGET)
    expect(second.height).toBeGreaterThanOrEqual(LABEL_TARGET)
  })

  it('counts a label with no room under the one above on that one', () => {
    const [cluster] = clusterLabels([
      { id: 'a', y: 100 },
      { id: 'b', y: 108 },
      { id: 'c', y: 140 },
    ])
    expect(cluster.shown).toEqual([
      { id: 'a', y: 100, extra: 1 },
      { id: 'c', y: 140, extra: 0 },
    ])
  })
})
