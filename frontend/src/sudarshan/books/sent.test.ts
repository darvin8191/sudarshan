/**
 * The app-wide hold on exits and cancels sent from the books (M6.9), on its
 * own: when a mark starts, what lets it go, when its time is up, and what a
 * reload brings back. The pages' side of it is sentExits.test.tsx and
 * sentCancels.test.tsx in cards/.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { noteOrderBook, notePushedFrame, resetOrderMemoryForTests } from './orderFold'
import {
  beginSends,
  cancelMark,
  exitMark,
  exitOrderStatuses,
  isHeld,
  NO_ANSWER_HOLD_MS,
  noteCancelBook,
  noteExitBook,
  noteExitOrders,
  outcomeOf,
  resetSentForTests,
  SENT_HOLD_MS,
  sendHeld,
  sentMarksNow,
  settleSends,
  TOLD_MS,
  toldOf,
} from './sent'

const ID = 'NFO:NIFTY27OCT2625900CE:MIS'
const LONG = { id: ID, was: 130, exchange: 'NFO', symbol: 'NIFTY27OCT2625900CE', product: 'MIS' }
const exit = (sandbox = false) => exitMark(sentMarksNow(), sandbox, ID)
const held = (sandbox = false) => isHeld(exit(sandbox))

/** Move the clock on and run what falls due. */
function wait(ms: number) {
  vi.advanceTimersByTime(ms)
}

/** A frame for the exit's own order, as the shell records it. */
function pushed(status: string, extra: Record<string, unknown> = {}) {
  notePushedFrame({
    mode: 'live',
    orderid: 'X1',
    symbol: 'NIFTY27OCT2625900CE',
    exchange: 'NFO',
    product: 'MIS',
    action: 'SELL',
    order_status: status,
    ...extra,
  })
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  vi.setSystemTime(new Date('2026-10-20T05:12:21Z'))
  resetOrderMemoryForTests()
  resetSentForTests()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('a send and its answer', () => {
  it('holds from the tap, and never runs out while in flight', () => {
    beginSends('exit', false, [LONG])
    expect(exit()?.state).toBe('sending')
    wait(10 * 60_000)
    expect(held()).toBe(true)
  })

  it('never runs out while in flight, whatever news of orders comes after its time', () => {
    beginSends('exit', false, [LONG])
    wait(SENT_HOLD_MS + 1_000)
    notePushedFrame({
      mode: 'live',
      orderid: 'Z9',
      symbol: 'SBIN',
      exchange: 'NSE',
      order_status: 'complete',
    })
    noteOrderBook([])
    expect(exit()).toMatchObject({ state: 'sending', expired: false })
  })

  it('lets go at once on a refusal; sent or not confirmed starts the clock', () => {
    const refused = beginSends('exit', false, [LONG])
    settleSends(refused, 'refused')
    expect(exit()).toBeUndefined()

    const unknown = beginSends('exit', false, [LONG])
    wait(3_000)
    settleSends(unknown, 'unconfirmed')
    expect(exit()).toMatchObject({ state: 'unconfirmed', since: Date.now() })
    wait(SENT_HOLD_MS - 1)
    expect(held()).toBe(true)
    wait(1)
    expect(held()).toBe(false)
    expect(exit()?.expired).toBe(true)
  })

  it('settles only its own marks: a late answer leaves a newer send alone', () => {
    const first = beginSends('exit', false, [LONG])
    const second = beginSends('exit', false, [LONG])
    settleSends(first, 'refused')
    expect(exit()?.batch).toBe(second)
    expect(held()).toBe(true)
  })

  it('reads a handler’s answer: false and "refused" are refusals; nothing said is sent', () => {
    expect(outcomeOf(false)).toBe('refused')
    expect(outcomeOf('refused')).toBe('refused')
    expect(outcomeOf('unconfirmed')).toBe('unconfirmed')
    expect(outcomeOf(true)).toBe('sent')
    expect(outcomeOf(undefined)).toBe('sent')
  })

  it('counts a handler that broke as having no answer: it may still be going', async () => {
    const outcome = await sendHeld('exit', false, [LONG], () => {
      throw new Error('handler broke')
    })
    expect(outcome).toBe('unanswered')
    expect(exit()).toMatchObject({ state: 'unconfirmed', noAnswer: true })
  })
})

describe('a send with no answer at all', () => {
  it('holds until 150 s after the tap: the server may still be placing it', () => {
    const batch = beginSends('exit', false, [LONG])
    wait(45_000) // the phone gave up
    settleSends(batch, 'unanswered')
    expect(exit()).toMatchObject({ state: 'unconfirmed', noAnswer: true })
    wait(NO_ANSWER_HOLD_MS - 45_000 - 1)
    expect(held()).toBe(true)
    wait(1)
    expect(held()).toBe(false)
  })

  it('lets go sooner when the book shows the position closed', () => {
    settleSends(beginSends('exit', false, [LONG]), 'unanswered')
    wait(20_000)
    noteExitBook(false, [{ id: ID, quantity: 0 }], Date.now())
    expect(exit()).toBeUndefined()
  })

  it('holds a cancel the same way, until its order is final', () => {
    settleSends(beginSends('cancel', false, [{ id: '1001' }]), 'unanswered')
    wait(60_000)
    expect(isHeld(cancelMark(sentMarksNow(), false, '1001'))).toBe(true)
    notePushedFrame({ mode: 'live', orderid: '1001', order_status: 'cancelled' })
    expect(cancelMark(sentMarksNow(), false, '1001')).toBeUndefined()
  })

  it('an answer the app could not confirm holds the usual 15 s', () => {
    settleSends(beginSends('exit', false, [LONG]), 'unconfirmed')
    expect(exit()?.noAnswer).toBe(false)
    wait(SENT_HOLD_MS)
    expect(held()).toBe(false)
  })
})

describe('an exit whose answer named its order', () => {
  it('is held by that order alone, not by another pushed on its side', () => {
    const batch = beginSends('exit', false, [LONG])
    pushed('open') // X1, its own, before the answer came
    settleSends(batch, 'sent', Date.now(), 'X1')
    expect(exit()).toMatchObject({ pinned: true, orders: [{ id: 'X1', at: Date.now() }] })
    // A stop-loss on the closing side, moved after the exit was sent.
    pushed('trigger pending', { orderid: 'SL9' })
    expect(exit()?.orders?.map((order) => order.id)).toEqual(['X1'])
    pushed('complete')
    wait(SENT_HOLD_MS)
    expect(held()).toBe(false)
  })

  it('waits on that order while a read has not said how it stands', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent', Date.now(), 'X7')
    wait(SENT_HOLD_MS)
    expect(exit()).toMatchObject({ byOrder: true, expired: false })
    noteOrderBook([
      {
        orderid: 'X7',
        symbol: 'NIFTY27OCT2625900CE',
        exchange: 'NFO',
        action: 'SELL',
        quantity: 130,
        price: 0,
        trigger_price: 0,
        pricetype: 'MARKET',
        product: 'MIS',
        order_status: 'complete',
        timestamp: '',
      },
    ])
    expect(held()).toBe(false)
  })
})

describe('an exit and the position book', () => {
  it('says nothing about it from a book asked for before it was sent', () => {
    const asked = Date.now() - 1
    const batch = beginSends('exit', false, [LONG])
    settleSends(batch, 'sent')
    noteExitBook(false, [], asked)
    expect(held()).toBe(true)
  })

  it.each([
    ['flat', [{ id: ID, quantity: 0 }]],
    ['gone', []],
    ['turned short', [{ id: ID, quantity: -65 }]],
  ])('lets it go when the position is %s', (_, rows) => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    noteExitBook(false, rows, Date.now())
    expect(exit()).toBeUndefined()
  })

  it('keeps it on a part closing, and starts its clock again there', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    wait(10_000)
    noteExitBook(false, [{ id: ID, quantity: 65 }], Date.now())
    expect(exit()).toMatchObject({ seen: 65, since: Date.now(), expired: false })
    wait(SENT_HOLD_MS - 1)
    expect(held()).toBe(true)
    wait(1)
    expect(held()).toBe(false)
  })

  it('holds again an exit whose time ran out, once the book shows it working', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    wait(SENT_HOLD_MS)
    expect(held()).toBe(false)
    noteExitBook(false, [{ id: ID, quantity: 65 }], Date.now())
    expect(held()).toBe(true)
  })

  it('starts nothing on a position grown by another order (an Add)', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    wait(SENT_HOLD_MS)
    noteExitBook(false, [{ id: ID, quantity: 195 }], Date.now())
    expect(held()).toBe(false)
    expect(exit()?.seen).toBe(195)
  })

  it('keeps the books apart: the sandbox’s book says nothing of a Live exit', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    noteExitBook(true, [], Date.now())
    expect(held(false)).toBe(true)
    expect(exit(true)).toBeUndefined()
  })
})

describe('an exit whose time is up', () => {
  it('waits while its own order works, pushed on the closing side since it was sent', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    pushed('open')
    expect(exit()?.orders).toEqual([{ id: 'X1', at: Date.now() }])
    wait(SENT_HOLD_MS - 1)
    expect(exit()?.byOrder).toBeFalsy()
    wait(1)
    // Held by that order alone now: what the page says, and reads the order book for.
    expect(exit()).toMatchObject({ byOrder: true, expired: false })
    wait(5 * 60_000)
    expect(held()).toBe(true)
    pushed('cancelled')
    expect(held()).toBe(false)
    expect(exitOrderStatuses(exit() as NonNullable<ReturnType<typeof exit>>)).toEqual(['cancelled'])
  })

  it('starts its clock again when the book shows it working, and is no longer held by order', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    pushed('open')
    wait(SENT_HOLD_MS)
    expect(exit()?.byOrder).toBe(true)
    noteExitBook(false, [{ id: ID, quantity: 65 }], Date.now())
    expect(exit()).toMatchObject({ byOrder: false, since: Date.now(), seen: 65 })
  })

  it('lets go when a read of its book, well after its order was pushed, no longer lists it', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    pushed('open')
    wait(SENT_HOLD_MS)
    // A read asked too soon after the push may not list it yet: it says nothing.
    noteExitOrders(false, [], Date.now() - SENT_HOLD_MS + 1_000)
    expect(exit()?.byOrder).toBe(true)
    // The other book's read says nothing of a Live exit.
    noteExitOrders(true, [], Date.now())
    expect(held()).toBe(true)
    noteExitOrders(false, [{ orderid: 'X1' }], Date.now())
    expect(held()).toBe(true)
    noteExitOrders(false, [{ orderid: 'OTHER' }], Date.now())
    expect(held()).toBe(false)
  })

  it('waits until a read of the order book shows that order ended', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    pushed('open')
    wait(SENT_HOLD_MS + 1_000)
    noteOrderBook([
      {
        orderid: 'X1',
        symbol: 'NIFTY27OCT2625900CE',
        exchange: 'NFO',
        action: 'SELL',
        quantity: 130,
        price: 0,
        trigger_price: 0,
        pricetype: 'MARKET',
        product: 'MIS',
        order_status: 'complete',
        timestamp: '',
      },
    ])
    expect(held()).toBe(false)
  })

  it('takes no lagging read over a final status the broker pushed', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    pushed('open')
    pushed('rejected')
    wait(10_000)
    // A read of the order book asked now, still showing the order as it was.
    noteOrderBook(
      [
        {
          orderid: 'X1',
          symbol: 'NIFTY27OCT2625900CE',
          exchange: 'NFO',
          action: 'SELL',
          quantity: 130,
          price: 0,
          trigger_price: 0,
          pricetype: 'MARKET',
          product: 'MIS',
          order_status: 'open',
          timestamp: '',
        },
      ],
      Date.now()
    )
    wait(SENT_HOLD_MS)
    expect(held()).toBe(false)
  })

  it.each([
    ['on the opening side', { action: 'BUY' }],
    ['for another product', { product: 'NRML' }],
    ['in the sandbox', { mode: 'analyze' }],
  ])('does not wait on a working order %s', (_, extra) => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    pushed('open', extra)
    wait(SENT_HOLD_MS)
    expect(held()).toBe(false)
  })

  it('does not wait on an order pushed before it was sent (a stop-loss already there)', () => {
    pushed('trigger pending', { orderid: 'SL1' })
    wait(1_000)
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    wait(SENT_HOLD_MS)
    expect(held()).toBe(false)
  })

  it('matches the order stream’s CNC to the book’s NRML (Upstox’s D product)', () => {
    const carried = { ...LONG, id: 'NFO:NIFTY27OCT2625900CE:NRML', product: 'NRML' }
    settleSends(beginSends('exit', false, [carried]), 'sent')
    pushed('open', { product: 'CNC' })
    wait(SENT_HOLD_MS)
    expect(isHeld(exitMark(sentMarksNow(), false, carried.id))).toBe(true)
  })

  it('is forgotten eight hours after it was sent', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    wait(SENT_HOLD_MS)
    expect(exit()?.expired).toBe(true)
    wait(8 * 60 * 60_000)
    expect(exit()).toBeUndefined()
  })
})

describe('a cancel', () => {
  it('lets go when its order is final, or gone, in its own book only', () => {
    settleSends(beginSends('cancel', false, [{ id: '1001' }, { id: '1002' }]), 'sent')
    noteCancelBook(true, [])
    expect(isHeld(cancelMark(sentMarksNow(), false, '1001'))).toBe(true)
    noteCancelBook(false, [
      { orderid: '1001', order_status: 'cancelled' },
      { orderid: '1002', order_status: 'open' },
    ])
    expect(cancelMark(sentMarksNow(), false, '1001')).toBeUndefined()
    expect(isHeld(cancelMark(sentMarksNow(), false, '1002'))).toBe(true)
    noteCancelBook(false, [])
    expect(cancelMark(sentMarksNow(), false, '1002')).toBeUndefined()
  })

  it('lets go on a final status pushed for its book, not for the other', () => {
    settleSends(beginSends('cancel', false, [{ id: '1001' }]), 'sent')
    notePushedFrame({ mode: 'analyze', orderid: '1001', order_status: 'cancelled' })
    expect(isHeld(cancelMark(sentMarksNow(), false, '1001'))).toBe(true)
    notePushedFrame({ mode: 'live', orderid: '1001', order_status: 'cancelled' })
    expect(cancelMark(sentMarksNow(), false, '1001')).toBeUndefined()
  })

  it('runs out 15 s after its answer, the order still working', () => {
    settleSends(beginSends('cancel', false, [{ id: '1001' }]), 'sent')
    notePushedFrame({ mode: 'live', orderid: '1001', order_status: 'open' })
    wait(SENT_HOLD_MS)
    expect(cancelMark(sentMarksNow(), false, '1001')?.expired).toBe(true)
  })
})

describe('a timer that ran late', () => {
  it('settles at once a mark it finds past its time', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    // The page was busy: the clock passed the hold before its timer could run, and
    // another send rescheduled first.
    vi.setSystemTime(Date.now() + SENT_HOLD_MS + 1_000)
    beginSends('cancel', false, [{ id: '1001' }])
    wait(0)
    expect(exit()?.expired).toBe(true)
  })
})

describe('an exit whose answer named every slice of it', () => {
  it('holds until the last slice has ended, by those orders alone', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent', Date.now(), ['X1', 'X2'])
    expect(exit()?.orders?.map((order) => order.id)).toEqual(['X1', 'X2'])
    pushed('complete') // X1
    pushed('open', { orderid: 'X2' })
    wait(SENT_HOLD_MS)
    expect(exit()).toMatchObject({ byOrder: true, expired: false })
    pushed('complete', { orderid: 'X2' })
    expect(exit()).toMatchObject({ expired: true })
  })

  it('takes them from a handler that names them', async () => {
    await sendHeld('exit', false, [LONG], async () => ({
      outcome: 'sent',
      orderid: 'X1',
      orderids: ['X1', 'X2', 'X3'],
    }))
    expect(exit()?.orders?.map((order) => order.id)).toEqual(['X1', 'X2', 'X3'])
    expect(exit()?.pinned).toBe(true)
  })
})

describe('a send that sent nothing', () => {
  /** An exit sent at 10:42:21 whose hold ran out with the book unchanged. */
  function ranOut(outcome: 'sent' | 'unanswered' = 'sent') {
    settleSends(beginSends('exit', false, [LONG]), outcome)
    wait(outcome === 'sent' ? SENT_HOLD_MS : NO_ANSWER_HOLD_MS)
    expect(exit()).toMatchObject({ expired: true })
    return exit()
  }

  it('refused, puts back the exit it replaced, warning and all', () => {
    const earlier = ranOut()
    const batch = beginSends('exit', false, [LONG])
    expect(exit()).toMatchObject({ state: 'sending', previous: earlier })
    settleSends(batch, 'refused')
    expect(exit()).toEqual(earlier)
  })

  it('refused with nothing before it, lets the row go', () => {
    settleSends(beginSends('exit', false, [LONG]), 'refused')
    expect(exit()).toBeUndefined()
  })

  it('refused because the app is still placing the earlier one, holds that one again from now', () => {
    const earlier = ranOut('unanswered')
    wait(2_000)
    const now = Date.now()
    settleSends(beginSends('exit', false, [LONG]), 'placing')
    expect(exit()).toMatchObject({
      sentAt: earlier.sentAt,
      noAnswer: true,
      expired: false,
      since: now,
      placingAt: now,
    })
    expect(exit()?.previous).toBeUndefined()
    expect(held()).toBe(true)
    wait(SENT_HOLD_MS - 1)
    expect(held()).toBe(true)
    wait(1)
    expect(exit()).toMatchObject({ expired: true, placingAt: now })
  })

  it('refused because the app is placing one this phone did not send, holds the row too', () => {
    const tap = Date.now()
    settleSends(beginSends('exit', false, [LONG]), 'placing')
    expect(exit()).toMatchObject({
      state: 'unconfirmed',
      sentAt: tap,
      placingAt: tap,
      expired: false,
    })
    wait(SENT_HOLD_MS)
    expect(held()).toBe(false)
  })

  it('answered, forgets the exit it replaced', () => {
    ranOut()
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    expect(exit()?.previous).toBeUndefined()
  })
})

describe('a send of several that says what became of each row', () => {
  it('settles each row by its own answer, and says why of one that sent nothing, for a while', () => {
    const PUT = { id: 'NFO:BANKNIFTY27OCT2657500PE:NRML', was: -30 }
    const batch = beginSends('exit', false, [LONG, PUT])
    const each = {
      [ID]: { outcome: 'sent' as const, orderids: ['251020000611111'] },
      [PUT.id]: { outcome: 'refused' as const, reason: 'An order is working to close it.' },
    }
    settleSends(batch, 'unconfirmed', Date.now(), null, each)
    expect(exit()?.state).toBe('sent')
    expect(exit()?.orders?.map((order) => order.id)).toEqual(['251020000611111'])
    expect(exitMark(sentMarksNow(), false, PUT.id)).toBeUndefined()
    const told = (now = Date.now()) =>
      toldOf(
        new Map([[`live:exit:${PUT.id}`, { text: 'old', at: now - TOLD_MS }]]),
        false,
        PUT.id,
        now
      )
    expect(told()).toBeNull() // a reason as old as TOLD_MS is not shown, whatever is kept
  })
})

describe('a reload', () => {
  async function reload() {
    vi.resetModules()
    return import('./sent')
  }

  it('brings back what the tab kept, a send in flight as not confirmed', async () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    beginSends('cancel', false, [{ id: '1001' }])
    const sentAt = Date.now()
    wait(2_000)

    const fresh = await reload()
    const now = Date.now()
    const marks = fresh.sentMarksNow()
    expect(fresh.exitMark(marks, false, ID)).toMatchObject({ state: 'sent', since: sentAt })
    expect(fresh.cancelMark(marks, false, '1001')).toMatchObject({
      state: 'unconfirmed',
      since: now,
    })
    fresh.resetSentForTests()
  })

  it('settles at once what ran past its time while the tab was away', async () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    beginSends('cancel', false, [{ id: '1001' }])
    settleSends(beginSends('cancel', false, [{ id: '1002' }]), 'sent')
    wait(2_000)
    // Discarded 2 s on, restored 75 s later: no timer of the old page ever ran.
    vi.setSystemTime(Date.now() + 75_000)

    const fresh = await reload()
    const marks = fresh.sentMarksNow()
    expect(fresh.exitMark(marks, false, ID)?.expired).toBe(true)
    expect(fresh.cancelMark(marks, false, '1002')?.expired).toBe(true)
    // The send in flight lost its answer: not confirmed, its clock from now.
    expect(fresh.cancelMark(marks, false, '1001')).toMatchObject({
      state: 'unconfirmed',
      expired: false,
    })
    fresh.resetSentForTests()
  })

  it('keeps an exit its own order holds, until a read of the order book says how it stands', async () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    pushed('open')
    vi.setSystemTime(Date.now() + 75_000)

    const fresh = await reload()
    const fold = await import('./orderFold')
    const mark = () => fresh.exitMark(fresh.sentMarksNow(), false, ID)
    // The reload forgot every push: until a read says otherwise, the order counts as working.
    expect(mark()).toMatchObject({ byOrder: true, expired: false })
    fold.noteOrderBook([
      {
        orderid: 'X1',
        symbol: 'NIFTY27OCT2625900CE',
        exchange: 'NFO',
        action: 'SELL',
        quantity: 130,
        price: 0,
        trigger_price: 0,
        pricetype: 'MARKET',
        product: 'MIS',
        order_status: 'rejected',
        timestamp: '',
      },
    ])
    expect(mark()?.expired).toBe(true)
    expect(fresh.exitOrderStatuses(mark() as NonNullable<ReturnType<typeof mark>>)).toEqual([
      'rejected',
    ])
    fresh.resetSentForTests()
  })

  it('keeps the longer hold of a send that got no answer', async () => {
    const batch = beginSends('exit', false, [LONG])
    wait(45_000)
    settleSends(batch, 'unanswered')
    wait(5_000)

    const fresh = await reload()
    const mark = () => fresh.exitMark(fresh.sentMarksNow(), false, ID)
    expect(mark()).toMatchObject({ noAnswer: true, expired: false })
    wait(SENT_HOLD_MS + 1_000) // 66 s from the tap: one that got an answer would let go
    expect(fresh.isHeld(mark())).toBe(true)
    wait(NO_ANSWER_HOLD_MS - 66_000 + 1_000)
    expect(fresh.isHeld(mark())).toBe(false)
    fresh.resetSentForTests()
  })

  it('keeps an exit pinned to the order its answer named', async () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent', Date.now(), 'X1')
    wait(2_000)

    const fresh = await reload()
    const fold = await import('./orderFold')
    const mark = () => fresh.exitMark(fresh.sentMarksNow(), false, ID)
    expect(mark()).toMatchObject({ pinned: true, orders: [{ id: 'X1' }] })
    const frame = { mode: 'live', symbol: 'NIFTY27OCT2625900CE', exchange: 'NFO', product: 'MIS' }
    fold.notePushedFrame({ ...frame, orderid: 'X1', action: 'SELL', order_status: 'complete' })
    // The trader's stop-loss on the same contract, moved after the reload.
    fold.notePushedFrame({
      ...frame,
      orderid: 'SL7',
      action: 'SELL',
      order_status: 'trigger pending',
    })
    wait(SENT_HOLD_MS)
    expect(mark()?.orders?.map((order) => order.id)).toEqual(['X1'])
    expect(fresh.isHeld(mark())).toBe(false)
    fresh.resetSentForTests()
  })

  it('keeps when the app said a row was still being placed', async () => {
    settleSends(beginSends('exit', false, [LONG]), 'placing')
    const placingAt = Date.now()
    wait(1_000)

    const fresh = await reload()
    expect(fresh.exitMark(fresh.sentMarksNow(), false, ID)).toMatchObject({
      placingAt,
      expired: false,
    })
    fresh.resetSentForTests()
  })

  it('forgets what a send still in flight replaced: no answer came for that send', async () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    wait(SENT_HOLD_MS)
    beginSends('exit', false, [LONG])
    wait(1_000)

    const fresh = await reload()
    const mark = fresh.exitMark(fresh.sentMarksNow(), false, ID)
    expect(mark).toMatchObject({ state: 'unconfirmed', noAnswer: true })
    expect(mark?.previous).toBeUndefined()
    fresh.resetSentForTests()
  })

  it('starts empty on storage it cannot read, and keeps working without it', async () => {
    sessionStorage.setItem('sudarshan-sent-v1', '{not json')
    let fresh = await reload()
    expect(fresh.sentMarksNow().size).toBe(0)

    const refuse = () => {
      throw new DOMException('denied', 'SecurityError')
    }
    vi.stubGlobal('sessionStorage', { getItem: refuse, setItem: refuse, removeItem: refuse })
    fresh = await reload()
    fresh.beginSends('exit', false, [LONG])
    expect(fresh.isHeld(fresh.exitMark(fresh.sentMarksNow(), false, ID))).toBe(true)
    fresh.resetSentForTests()
  })
})
