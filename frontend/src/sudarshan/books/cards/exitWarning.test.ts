/**
 * What Exit's confirmation says when it is offered again after an earlier
 * exit's time ran out (M6.9): what became of that exit, as far as the book and
 * its own orders (pushed or read) say, and what this one would do. And what a
 * tap on a held Exit says instead of sending.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

import { notePushedFrame, resetOrderMemoryForTests } from '@/sudarshan/books/orderFold'
import {
  beginSends,
  exitMark,
  NO_ANSWER_HOLD_MS,
  noteExitBook,
  resetSentForTests,
  SENT_HOLD_MS,
  type SentMark,
  sentMarksNow,
  settleSends,
} from '@/sudarshan/books/sent'
import { exitWarning, heldWhy } from './PositionCards'

const ID = 'NFO:NIFTY27OCT2625900CE:MIS'
const LONG = { id: ID, was: 130, exchange: 'NFO', symbol: 'NIFTY27OCT2625900CE', product: 'MIS' }
const mark = () => exitMark(sentMarksNow(), false, ID) as SentMark

/** The exit's own order, pushed with `status`. */
function pushed(status: string) {
  notePushedFrame({
    mode: 'live',
    orderid: 'X1',
    symbol: 'NIFTY27OCT2625900CE',
    exchange: 'NFO',
    product: 'MIS',
    action: 'SELL',
    order_status: status,
  })
}

/** Sent at 10:42:21, and the book still at `seen` when its time ran out. */
function ranOut(steps: Array<() => void> = []) {
  settleSends(beginSends('exit', false, [LONG]), 'sent')
  for (const step of steps) step()
  vi.advanceTimersByTime(SENT_HOLD_MS)
  expect(mark().expired).toBe(true)
}

const partFill = () => noteExitBook(false, [{ id: ID, quantity: 65 }], Date.now())

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  // 10:42:21 in India.
  vi.setSystemTime(new Date('2026-10-20T05:12:21Z'))
  resetOrderMemoryForTests()
  resetSentForTests()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('Exit offered again says what became of the earlier exit', () => {
  it('says nothing while that exit is still held', () => {
    settleSends(beginSends('exit', false, [LONG]), 'sent')
    expect(exitWarning(mark())).toBeNull()
    expect(exitWarning(undefined)).toBeNull()
  })

  it('rejected or cancelled, as Orders shows it: it closed nothing', () => {
    ranOut([() => pushed('open'), () => pushed('rejected')])
    expect(exitWarning(mark())).toBe(
      'Orders shows the exit sent at 10:42:21 rejected: it closed nothing. This sends a new exit.'
    )
  })

  it('part of it filled, the rest cancelled: how much is left', () => {
    ranOut([() => pushed('open'), partFill, () => pushed('cancelled')])
    expect(exitWarning(mark())).toBe(
      'The exit sent at 10:42:21 closed 65 of 130, and Orders shows the rest cancelled. This sends an exit for the 65 left.'
    )
  })

  it('part of it filled, nothing known of the rest: it may still be working', () => {
    ranOut([partFill])
    expect(exitWarning(mark())).toBe(
      'The exit sent at 10:42:21 has closed 65 of 130 so far. If the rest is still open in Orders, this sends a second order for it.'
    )
  })

  it('filled, the book not caught up: a second exit would turn the position', () => {
    ranOut([() => pushed('open'), () => pushed('complete')])
    expect(exitWarning(mark())).toBe(
      'Orders shows the exit sent at 10:42:21 filled, but the book still shows this position. Check Positions first: a second exit would turn it the other way.'
    )
  })

  it('nothing known: it has not shown, and may still be open', () => {
    ranOut()
    expect(exitWarning(mark())).toBe(
      'The exit sent at 10:42:21 has not shown in the book yet. If that order is still open in Orders, this sends a second one.'
    )
    resetSentForTests()
    settleSends(beginSends('exit', false, [LONG]), 'unconfirmed')
    vi.advanceTimersByTime(SENT_HOLD_MS)
    // Sent 15 s after the first.
    expect(exitWarning(mark())).toBe(
      'The exit sent at 10:42:36 was not confirmed and has not shown in the book yet. If that order is still open in Orders, this sends a second one.'
    )
  })
})

describe('an exit that got no answer at all', () => {
  it('says so when offered again, after its longer hold', () => {
    settleSends(beginSends('exit', false, [LONG]), 'unanswered')
    vi.advanceTimersByTime(NO_ANSWER_HOLD_MS)
    expect(exitWarning(mark())).toBe(
      'The exit sent at 10:42:21 got no answer and has not shown in the book yet. If that order is still open in Orders, this sends a second one.'
    )
  })

  it('a tap while it holds says to wait for it, not to look in Orders yet', () => {
    settleSends(beginSends('exit', false, [LONG]), 'unanswered')
    expect(heldWhy(mark())).toBe(
      'An exit for NIFTY 25900 CE was sent at 10:42:21 and no answer has come yet: it may still be going through. Wait for Positions or Orders to show it before sending another.'
    )
  })
})

describe('a tap on a held Exit says why it sends nothing', () => {
  it.each([
    ['in flight', () => beginSends('exit', false, [LONG]), 'is still being sent'],
    [
      'sent, unseen',
      () => settleSends(beginSends('exit', false, [LONG]), 'sent'),
      'was sent at 10:42:21 and has not shown in the book yet',
    ],
    [
      'not confirmed',
      () => settleSends(beginSends('exit', false, [LONG]), 'unconfirmed'),
      'was sent at 10:42:21 and is not confirmed',
    ],
    [
      'part filled',
      () => {
        settleSends(beginSends('exit', false, [LONG]), 'sent')
        partFill()
      },
      'was sent at 10:42:21 and has closed 65 of 130 so far',
    ],
    [
      'held by its own order',
      () => {
        settleSends(beginSends('exit', false, [LONG]), 'sent')
        pushed('open')
        vi.advanceTimersByTime(SENT_HOLD_MS)
      },
      'was sent at 10:42:21 and its order is still open',
    ],
  ])('%s', (_, make, words) => {
    make()
    expect(heldWhy(mark())).toContain(`An exit for NIFTY 25900 CE ${words}`)
  })
})
