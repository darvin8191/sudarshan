/**
 * The order book takes the broker's order updates at once (M6.1): a fill or
 * a rejection changes its row the moment it is pushed, and a re-read that has
 * not caught up yet cannot put the row back to "open".
 */
import { describe, expect, it } from 'vitest'
import type { Order } from '@/types/trading'
import {
  foldOrderUpdate,
  frameFitsMode,
  holdPushed,
  PUSH_HOLD_MS,
  type Pushed,
  rememberPushed,
} from './orderFold'

const OPEN: Order = {
  symbol: 'NIFTY28OCT2525900CE',
  exchange: 'NFO',
  action: 'BUY',
  quantity: 65,
  price: 120,
  trigger_price: 0,
  pricetype: 'LIMIT',
  product: 'NRML',
  orderid: '251020000598204',
  order_status: 'open',
  timestamp: '20-Oct-2026 10:15:02',
}

describe('foldOrderUpdate', () => {
  it("changes the order's row the moment the broker pushes a fill", () => {
    const [row] = foldOrderUpdate([OPEN], {
      orderid: '251020000598204',
      order_status: 'complete',
      filled_quantity: 65,
    })
    expect(row.order_status).toBe('complete')
    expect(row.price).toBe(120)
  })

  it('takes a modify made in the broker app: the new price and quantity', () => {
    const [row] = foldOrderUpdate([OPEN], {
      orderid: '251020000598204',
      order_status: 'open',
      price: 118.5,
      quantity: 130,
    })
    expect(row).toMatchObject({ price: 118.5, quantity: 130, order_status: 'open' })
  })

  it('leaves an order it has not read to the re-read', () => {
    const book = [OPEN]
    expect(foldOrderUpdate(book, { orderid: 'unknown', order_status: 'complete' })).toBe(book)
  })
})

describe('holdPushed', () => {
  it('keeps a pushed fill over a re-read that still says open, for a few seconds', () => {
    const pushed = new Map<string, Pushed>()
    rememberPushed(pushed, { orderid: OPEN.orderid, order_status: 'complete' }, 1_000)

    expect(holdPushed([OPEN], pushed, 2_000)[0].order_status).toBe('complete')
    // Once the hold has passed, the re-read is taken as it is.
    expect(holdPushed([OPEN], pushed, 1_000 + PUSH_HOLD_MS + 1)[0].order_status).toBe('open')
  })

  it('takes a re-read that has caught up or says something new', () => {
    const pushed = new Map<string, Pushed>()
    rememberPushed(pushed, { orderid: OPEN.orderid, order_status: 'complete' }, 1_000)
    const rejected = { ...OPEN, order_status: 'rejected' as const }
    expect(holdPushed([rejected], pushed, 1_500)[0].order_status).toBe('rejected')
  })
})

describe('frameFitsMode', () => {
  it('gives the live book the broker frames and the analyze book the sandbox frames', () => {
    expect(frameFitsMode({ mode: 'live' }, 'live')).toBe(true)
    expect(frameFitsMode({ mode: 'live' }, 'analyzer')).toBe(false)
    expect(frameFitsMode({ mode: 'analyze' }, 'analyzer')).toBe(true)
    expect(frameFitsMode({ mode: 'analyze' }, 'live')).toBe(false)
  })
})
