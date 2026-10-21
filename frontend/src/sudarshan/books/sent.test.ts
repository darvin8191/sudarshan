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
  isHeld,
  noteCancelBook,
  noteExitBook,
  outcomeOf,
  resetSentForTests,
  SENT_HOLD_MS,
  sendHeld,
  sentMarksNow,
  settleSends,
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

  it('counts a handler that broke as not confirmed', async () => {
    const outcome = await sendHeld('exit', false, [LONG], () => {
      throw new Error('handler broke')
    })
    expect(outcome).toBe('unconfirmed')
    expect(exit()?.state).toBe('unconfirmed')
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
    wait(SENT_HOLD_MS + 5_000)
    expect(held()).toBe(true)
    pushed('cancelled')
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
