/**
 * The kill switch sheet's holds (M20.7): what it holds from the tap, in which book, and how
 * each hold settles on the answer. They are the app-wide sent marks the books read, so a held
 * row offers no second exit or cancel until its book shows it settled.
 */
import { afterEach, describe, expect, it } from 'vitest'
import type { SwitchAnswer } from '@/api/risk'
import { positionId } from '@/sudarshan/books/cards/PositionCards'
import {
  cancelMark,
  exitMark,
  isHeld,
  resetSentForTests,
  sentMarksNow,
} from '@/sudarshan/books/sent'
import type { Order, Position } from '@/types/trading'
import { actsOn, outcomesOf, positionKey, targetsOf, turnOnHeld } from './holds'

afterEach(() => resetSentForTests())

const CE = { symbol: 'NIFTY27OCT2625900CE', exchange: 'NFO', product: 'MIS' as const }
const POSITIONS: Position[] = [
  { ...CE, quantity: 65, average_price: 120, ltp: 118, pnl: -130, pnlpercent: -1.6 },
  {
    symbol: 'SBIN',
    exchange: 'NSE',
    product: 'CNC',
    quantity: 0,
    average_price: 0,
    ltp: 800,
    pnl: 0,
    pnlpercent: 0,
  },
  {
    symbol: 'RELIANCE',
    exchange: 'NSE',
    product: 'MIS',
    quantity: -10,
    average_price: 1400,
    ltp: 1390,
    pnl: 100,
    pnlpercent: 0.7,
  },
]
const ORDER = {
  symbol: 'X',
  exchange: 'NSE',
  action: 'BUY',
  quantity: 1,
  price: 1,
  trigger_price: 0,
  pricetype: 'LIMIT',
  product: 'MIS',
  timestamp: '',
} as const
const ORDERS: Order[] = [
  { ...ORDER, orderid: 'A1', order_status: 'open' },
  { ...ORDER, orderid: 'A2', order_status: 'trigger pending' },
  { ...ORDER, orderid: 'A3', order_status: 'complete' },
  { ...ORDER, orderid: 'A4', order_status: 'cancelled' },
]

function answer(overrides: Partial<SwitchAnswer> = {}, steps: object[] = []): SwitchAnswer {
  return {
    code: 200,
    ok: true,
    message: 'Kill switch on: new orders are refused; exits and cancels still go.',
    activated: true,
    state: { known: true, on: true, report: { activation_id: 'ks-1', steps: steps as never } },
    ...overrides,
  }
}

describe('what the switch holds', () => {
  it('addresses a position as the books do', () => {
    for (const position of POSITIONS) expect(positionKey(position)).toBe(positionId(position))
  })

  it('takes the open positions and the working orders, nothing closed or final', () => {
    const { exits, cancels } = targetsOf({ positions: POSITIONS, orders: ORDERS })
    expect(exits.map((target) => [target.id, target.was])).toEqual([
      ['NFO:NIFTY27OCT2625900CE:MIS', 65],
      ['NSE:RELIANCE:MIS', -10],
    ])
    expect(cancels.map((target) => target.id)).toEqual(['A1', 'A2'])
  })

  it('acts on the sandbox only while the guard is at sandbox, on both when it is on', () => {
    expect([actsOn('on', false), actsOn('on', true)]).toEqual([true, true])
    expect([actsOn('sandbox', false), actsOn('sandbox', true)]).toEqual([false, true])
    expect([actsOn('off', true), actsOn(undefined, true)]).toEqual([false, false])
  })
})

describe('how each hold settles', () => {
  it('is let go at once when this tap turned nothing on', () => {
    const refusals = [
      answer({ code: 400, ok: false, activated: false, state: null }),
      answer({ activated: false }), // already on
      answer({ code: 409, ok: false, activated: false }),
      answer({ code: 503, ok: false, activated: false, state: null }),
    ]
    for (const refused of refusals) {
      expect(outcomesOf(refused, false)).toEqual({ exit: 'refused', cancel: 'refused', each: null })
    }
  })

  it('stays held for as long as no answer comes', () => {
    expect(outcomesOf(null, false)).toEqual({
      exit: 'unanswered',
      cancel: 'unanswered',
      each: null,
    })
  })

  it("reads its own book's steps", () => {
    const steps = [
      { step: 'live_orders', ok: true, words: '2 open orders cancelled.' },
      { step: 'live_exit_all', ok: false, words: 'Exit all: not confirmed.' },
      { step: 'sandbox_orders', ok: true, words: 'No open sandbox orders to cancel.' },
    ]
    expect(outcomesOf(answer({}, steps), false)).toMatchObject({
      exit: 'unconfirmed',
      cancel: 'sent',
    })
    // No sandbox Exit all was asked for: nothing was sent to hold.
    expect(outcomesOf(answer({}, steps), true)).toMatchObject({ exit: 'refused', cancel: 'sent' })
    const busy = [{ step: 'live_exit_all', ok: false, words: 'busy', detail: { status: 409 } }]
    expect(outcomesOf(answer({}, busy), false).exit).toBe('placing')
    const second = [
      { step: 'live_orders', ok: false, words: 'not confirmed' },
      { step: 'live_orders_again', ok: true, words: 'Looked again two seconds later: done.' },
    ]
    expect(outcomesOf(answer({}, second), false).cancel).toBe('sent')
  })

  it("takes Exit all's word on each position", () => {
    const positions = [
      { ...CE, outcome: 'sent', orderids: ['261027000001'] },
      {
        exchange: 'NSE',
        symbol: 'RELIANCE',
        product: 'MIS',
        outcome: 'not_sent',
        reason:
          'RELIANCE has a BUY 10 SL-M order working to close it. Cancel it in Orders, then exit.',
      },
    ]
    const steps = [
      { step: 'live_exit_all', ok: false, words: 'Exit all: ...', detail: { positions } },
    ]
    expect(outcomesOf(answer({}, steps), false).each).toEqual({
      'NFO:NIFTY27OCT2625900CE:MIS': { outcome: 'sent', orderids: ['261027000001'] },
      'NSE:RELIANCE:MIS': { outcome: 'refused', reason: positions[1].reason },
    })
  })
})

describe('turning it on with the rows held', () => {
  const book = { positions: POSITIONS, orders: ORDERS }

  it('holds from the tap and settles each position by its own outcome', async () => {
    let during: ReturnType<typeof sentMarksNow> | null = null
    await turnOnHeld({
      book,
      sandbox: false,
      stage: 'on',
      exitAll: true,
      send: async () => {
        during = sentMarksNow()
        return answer({}, [
          { step: 'live_orders', ok: true, words: 'cancelled' },
          {
            step: 'live_exit_all',
            ok: false,
            words: 'Exit all did not close every position.',
            detail: {
              positions: [
                { ...CE, outcome: 'sent', orderids: ['261027000001'] },
                {
                  exchange: 'NSE',
                  symbol: 'RELIANCE',
                  product: 'MIS',
                  outcome: 'not_sent',
                  reason: 'RELIANCE already has an exit on its way.',
                },
              ],
            },
          },
        ])
      },
    })
    const tapped = during as unknown as ReturnType<typeof sentMarksNow>
    expect(exitMark(tapped, false, 'NFO:NIFTY27OCT2625900CE:MIS')?.state).toBe('sending')
    expect(cancelMark(tapped, false, 'A1')?.state).toBe('sending')
    const after = sentMarksNow()
    expect(exitMark(after, false, 'NFO:NIFTY27OCT2625900CE:MIS')?.state).toBe('sent')
    expect(exitMark(after, false, 'NSE:RELIANCE:MIS')).toBeUndefined() // nothing went for it
    expect(isHeld(cancelMark(after, false, 'A2'))).toBe(true)
  })

  it('holds no position without Exit all, and lets everything go on a refusal', async () => {
    await turnOnHeld({
      book,
      sandbox: false,
      stage: 'on',
      exitAll: false,
      send: async () => {
        expect(exitMark(sentMarksNow(), false, 'NSE:RELIANCE:MIS')).toBeUndefined()
        expect(cancelMark(sentMarksNow(), false, 'A1')?.state).toBe('sending')
        return answer({ code: 400, ok: false, activated: false, state: null })
      },
    })
    expect(cancelMark(sentMarksNow(), false, 'A1')).toBeUndefined()
  })

  it('never holds Live while the guard acts on the sandbox only', async () => {
    await turnOnHeld({
      book,
      sandbox: false,
      stage: 'sandbox',
      exitAll: true,
      send: async () => {
        expect(sentMarksNow().size).toBe(0)
        return answer()
      },
    })
    expect(sentMarksNow().size).toBe(0)
  })

  it('keeps the rows held when the call fails without an answer', async () => {
    const result = await turnOnHeld({
      book,
      sandbox: true,
      stage: 'sandbox',
      exitAll: true,
      send: async () => {
        throw new Error('timeout')
      },
    })
    expect(result).toBeNull()
    const mark = exitMark(sentMarksNow(), true, 'NFO:NIFTY27OCT2625900CE:MIS')
    expect(mark?.state).toBe('unconfirmed')
    expect(mark?.noAnswer).toBe(true)
  })
})
