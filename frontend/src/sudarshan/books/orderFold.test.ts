/**
 * The order book takes the broker's order updates at once (M6.1): a fill or
 * a rejection changes its row the moment it is pushed, a re-read that has not
 * caught up yet cannot put the row back to "open", a frame delivered out of
 * order cannot either, and a status outside the book's own words leaves the
 * row (and its Cancel and Modify) as it was.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import type { Order } from '@/types/trading'
import {
  cancelAllTargets,
  foldOrderUpdate,
  frameFitsMode,
  holdPushed,
  noteOrderBook,
  notePushedFrame,
  PUSH_HOLD_MS,
  type Pushed,
  pushedStatuses,
  recentPush,
  rememberPushed,
  resetOrderMemoryForTests,
  workingOrderIds,
} from './orderFold'

beforeEach(() => {
  resetOrderMemoryForTests()
})

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

  it('never moves a filled order back to open on a frame delivered late', () => {
    const complete = { orderid: OPEN.orderid, order_status: 'complete', filled_quantity: 65 }
    const lateOpen = { orderid: OPEN.orderid, order_status: 'open', price: 119, quantity: 130 }
    const filled = foldOrderUpdate([OPEN], complete)
    const after = foldOrderUpdate(filled, lateOpen)
    expect(after).toBe(filled)
    expect(after[0]).toMatchObject({ order_status: 'complete', price: 120, quantity: 65 })
  })

  it("keeps the row's status, and so its Cancel and Modify, for a status the book does not use", () => {
    for (const order_status of ['not cancelled', 'not modified', 'validation pending']) {
      const [row] = foldOrderUpdate([OPEN], { orderid: OPEN.orderid, order_status, price: 121 })
      expect(row).toMatchObject({ order_status: 'open', price: 121 })
    }
  })

  it("writes the broker's other spellings in the book's words", () => {
    const fold = (order_status: string) =>
      foldOrderUpdate([OPEN], { orderid: OPEN.orderid, order_status })[0].order_status
    expect(fold('canceled')).toBe('cancelled')
    expect(fold('COMPLETE')).toBe('complete')
    expect(fold('trigger pending')).toBe('trigger pending')
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

  it('keeps the hold over a lagging re-read when a late "open" follows the fill', () => {
    const pushed = new Map<string, Pushed>()
    rememberPushed(pushed, { orderid: OPEN.orderid, order_status: 'complete' }, 1_000)
    // The server's event bus can emit frames out of order.
    rememberPushed(pushed, { orderid: OPEN.orderid, order_status: 'open' }, 1_010)
    expect(holdPushed([OPEN], pushed, 2_000)[0].order_status).toBe('complete')
  })

  it('takes a re-read that has caught up or says something new', () => {
    const pushed = new Map<string, Pushed>()
    rememberPushed(pushed, { orderid: OPEN.orderid, order_status: 'complete' }, 1_000)
    const rejected = { ...OPEN, order_status: 'rejected' as const }
    expect(holdPushed([rejected], pushed, 1_500)[0].order_status).toBe('rejected')
  })
})

describe('what the app knows about orders, whatever page is open', () => {
  it('keeps what was pushed, so a page opened a moment later holds it over its first read', () => {
    notePushedFrame({ orderid: OPEN.orderid, order_status: 'complete' }, 1_000)
    expect(holdPushed([OPEN], pushedStatuses(), 1_500)[0].order_status).toBe('complete')
    expect(recentPush(2_500, 2_000)?.frame.orderid).toBe(OPEN.orderid)
    expect(recentPush(2_500, 4_000)).toBeNull()
  })

  it('knows which orders are working, from the books read and the frames pushed', () => {
    noteOrderBook(
      [
        OPEN,
        { ...OPEN, orderid: 'B', order_status: 'trigger pending' },
        { ...OPEN, orderid: 'C', order_status: 'complete' },
      ],
      1_000
    )
    expect(workingOrderIds().sort()).toEqual([OPEN.orderid, 'B'].sort())

    notePushedFrame({ orderid: 'B', order_status: 'cancelled' }, 2_000)
    notePushedFrame({ orderid: 'D', order_status: 'open' }, 2_100)
    expect(workingOrderIds().sort()).toEqual([OPEN.orderid, 'D'].sort())
    // A book read that trails the push does not reopen it.
    noteOrderBook([{ ...OPEN, orderid: 'B', order_status: 'open' }], 2_200)
    expect(workingOrderIds()).not.toContain('B')
    // What a cancel-all just reported covered: the working ones, and one its echo already closed.
    expect(cancelAllTargets(15_000, 3_000).sort()).toEqual([OPEN.orderid, 'B', 'D'].sort())
    expect(cancelAllTargets(15_000, 30_000)).not.toContain('B')
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
