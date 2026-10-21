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
  reasonOf,
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

describe('foldOrderUpdate: the fill and the reason', () => {
  const ID = OPEN.orderid

  it('takes the filled count, the average and the rest the moment they are pushed', () => {
    const [row] = foldOrderUpdate([OPEN], {
      orderid: ID,
      order_status: 'open',
      filled_quantity: 30,
      pending_quantity: 35,
      average_price: 119.95,
    })
    expect(row).toMatchObject({ filled_quantity: 30, pending_quantity: 35, average_price: 119.95 })
  })

  it('never lowers the filled count: a frame naming fewer filled is older, all of it', () => {
    const later = foldOrderUpdate([OPEN], {
      orderid: ID,
      order_status: 'open',
      filled_quantity: 40,
      average_price: 120,
    })
    const after = foldOrderUpdate(later, {
      orderid: ID,
      order_status: 'open',
      filled_quantity: 10,
      average_price: 119.5,
      price: 117,
    })
    expect(after).toBe(later)
    expect(after[0]).toMatchObject({ filled_quantity: 40, average_price: 120, price: 120 })
  })

  it('leaves an average of zero out: nothing known about it', () => {
    const [row] = foldOrderUpdate([OPEN], {
      orderid: ID,
      order_status: 'open',
      filled_quantity: 0,
      average_price: 0,
    })
    expect(row).toMatchObject({ filled_quantity: 0 })
    expect(row).not.toHaveProperty('average_price')
  })

  it('takes the broker’s reason with a rejection', () => {
    const [row] = foldOrderUpdate([OPEN], {
      orderid: ID,
      order_status: 'rejected',
      rejection_reason: 'RMS:Margin Exceeds',
    })
    expect(row).toMatchObject({ order_status: 'rejected', rejection_reason: 'RMS:Margin Exceeds' })
  })

  it('takes no reason from a frame whose reason is only the status word', () => {
    const [row] = foldOrderUpdate([OPEN], {
      orderid: ID,
      order_status: 'rejected',
      rejection_reason: 'rejected',
    })
    expect(row.order_status).toBe('rejected')
    expect(row).not.toHaveProperty('rejection_reason')
    expect(reasonOf('REJECTED')).toBe('')
    expect(reasonOf(' cancelled ')).toBe('')
    expect(reasonOf('RMS: rejected for margin')).toBe('RMS: rejected for margin')
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

  it('holds a pushed fill’s count and average with its status over a lagging re-read', () => {
    const pushed = new Map<string, Pushed>()
    const fill = { filled_quantity: 65, average_price: 120.05 }
    rememberPushed(pushed, { orderid: OPEN.orderid, order_status: 'complete', ...fill }, 1_000)
    expect(holdPushed([OPEN], pushed, 2_000)[0]).toMatchObject({
      order_status: 'complete',
      ...fill,
    })
  })

  it('keeps a pushed rejection’s reason over a re-read that has none', () => {
    const pushed = new Map<string, Pushed>()
    const reason = 'RMS:Margin Exceeds'
    rememberPushed(
      pushed,
      { orderid: OPEN.orderid, order_status: 'rejected', rejection_reason: reason },
      1_000
    )
    // Lagging, then caught up with an empty reason: the pushed words stay either way.
    expect(holdPushed([OPEN], pushed, 1_500)[0]).toMatchObject({ rejection_reason: reason })
    const caughtUp = { ...OPEN, order_status: 'rejected' as const, rejection_reason: '' }
    expect(holdPushed([caughtUp], pushed, 60_000)[0]).toMatchObject({ rejection_reason: reason })
  })

  it('fills what a re-read leaves out from the frames, long after the hold', () => {
    const pushed = new Map<string, Pushed>()
    rememberPushed(
      pushed,
      { orderid: OPEN.orderid, order_status: 'open', filled_quantity: 30, average_price: 119.9 },
      1_000
    )
    // Upstox's REST book carries no fills: the pushed part fill stays on the row.
    const [row] = holdPushed([OPEN], pushed, 60_000)
    expect(row).toMatchObject({ order_status: 'open', filled_quantity: 30, average_price: 119.9 })
    // A book that carries its own (the sandbox's) is taken as it is.
    const own = { ...OPEN, filled_quantity: 35, average_price: 120 }
    expect(holdPushed([own], pushed, 60_000)[0]).toMatchObject({
      filled_quantity: 35,
      average_price: 120,
    })
  })

  it('keeps what filled before a cancel, as the frames built it up', () => {
    const pushed = new Map<string, Pushed>()
    const id = OPEN.orderid
    rememberPushed(
      pushed,
      { orderid: id, order_status: 'open', filled_quantity: 30, average_price: 119.9 },
      1_000
    )
    rememberPushed(pushed, { orderid: id, order_status: 'cancelled' }, 2_000)
    const cancelled = { ...OPEN, order_status: 'cancelled' as const }
    expect(holdPushed([cancelled], pushed, 3_000)[0]).toMatchObject({
      order_status: 'cancelled',
      filled_quantity: 30,
      average_price: 119.9,
    })
  })

  it('does not put an earlier part fill on an order the book says is complete', () => {
    const pushed = new Map<string, Pushed>()
    rememberPushed(
      pushed,
      { orderid: OPEN.orderid, order_status: 'open', filled_quantity: 30, average_price: 119.9 },
      1_000
    )
    const complete = { ...OPEN, order_status: 'complete' as const }
    const [row] = holdPushed([complete], pushed, 60_000)
    expect(row).not.toHaveProperty('filled_quantity')
    expect(row).not.toHaveProperty('average_price')
  })

  it('never lowers the count it remembers, whatever order the frames come in', () => {
    const pushed = new Map<string, Pushed>()
    const id = OPEN.orderid
    rememberPushed(pushed, { orderid: id, order_status: 'open', filled_quantity: 40 }, 1_000)
    rememberPushed(pushed, { orderid: id, order_status: 'open', filled_quantity: 10 }, 1_010)
    expect(pushed.get(id)?.filled_quantity).toBe(40)
  })

  it('keeps a pushed "trigger pending" over the REST book’s "open" until a frame says otherwise', () => {
    const pushed = new Map<string, Pushed>()
    const id = OPEN.orderid
    rememberPushed(pushed, { orderid: id, order_status: 'trigger pending' }, 1_000)
    // Upstox's REST book says "open" for an order waiting for its trigger, however long it waits.
    expect(holdPushed([OPEN], pushed, 1_000 + 10 * PUSH_HOLD_MS)[0].order_status).toBe(
      'trigger pending'
    )
    // Triggered: the broker pushes "open", and the row says so.
    rememberPushed(pushed, { orderid: id, order_status: 'open' }, 2_000)
    expect(holdPushed([OPEN], pushed, 3_000)[0].order_status).toBe('open')
  })

  it('remembers a bounded number of orders, oldest forgotten first', () => {
    const pushed = new Map<string, Pushed>()
    for (let n = 0; n < 2_100; n += 1) {
      rememberPushed(pushed, { orderid: `o${n}`, order_status: 'open', filled_quantity: 1 }, n)
    }
    expect(pushed.size).toBe(2_000)
    expect(pushed.has('o0')).toBe(false)
    expect(pushed.has('o2099')).toBe(true)
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
