import type { KillSwitchStep, RiskStage, SwitchAnswer } from '@/api/risk'
import { isFinalStatus } from '@/sudarshan/books/orderFold'
import { exitAllEach } from '@/sudarshan/books/sendOutcome'
import {
  beginSends,
  type EachAnswer,
  type SendOutcome,
  type SendTarget,
  settleSends,
} from '@/sudarshan/books/sent'
import type { Order, Position } from '@/types/trading'

/**
 * The kill switch is a destructive action like Exit all and Cancel all, so its sheet holds
 * the rows it acts on in the app-wide sent marks (sudarshan/books/sent.ts) from the tap: each
 * open order it will cancel and, with Exit all ticked, each open position. Positions and
 * Orders then say "sent" and offer no second exit until the book shows each one settled,
 * exactly as after their own Exit all. What the server answered says how each hold settles:
 * a refusal (wrong phrase, already on, off on this server) lets go at once, and Exit all's
 * own word on each position (sent, not confirmed, or nothing sent and why) settles that row.
 *
 * It holds only the book the page shows (Live, or the sandbox in Analyze mode), and only when
 * the switch acts on that book: with the guard at "sandbox" it never touches Live.
 */

export interface Book {
  positions: Position[]
  orders: Order[]
}

/** A position as the books address it (books/cards/PositionCards.tsx `positionId`). */
export function positionKey(position: Pick<Position, 'exchange' | 'symbol' | 'product'>): string {
  return `${position.exchange}:${position.symbol}:${position.product}`
}

/** Whether turning the switch on cancels and exits in this book. */
export function actsOn(stage: RiskStage | undefined, sandbox: boolean): boolean {
  return stage === 'on' || (stage === 'sandbox' && sandbox)
}

/** The open positions Exit all closes, and the working orders the cancel takes. */
export function targetsOf(book: Book): { exits: SendTarget[]; cancels: SendTarget[] } {
  const exits = book.positions
    .filter((position) => (Number(position.quantity) || 0) !== 0)
    .map((position) => ({
      id: positionKey(position),
      was: Number(position.quantity) || 0,
      exchange: position.exchange,
      symbol: position.symbol,
      product: position.product,
    }))
  const cancels = book.orders
    .filter((order) => order.orderid && !isFinalStatus(String(order.order_status ?? '')))
    .map((order) => ({ id: String(order.orderid) }))
  return { exits, cancels }
}

/** The report's steps that say what became of each book's Exit all and cancel. */
const STEPS = {
  live: { exit: ['live_exit_all'], cancel: ['live_orders', 'live_orders_again'] },
  sandbox: { exit: ['sandbox_exit_all'], cancel: ['sandbox_orders'] },
} as const

function named(answer: SwitchAnswer, names: readonly string[]): KillSwitchStep[] {
  return (answer.state?.report?.steps ?? []).filter((step) => names.includes(step.step))
}

function outcomeOfSteps(steps: KillSwitchStep[]): SendOutcome {
  if (steps.length === 0) return 'refused' // not asked, or not reached: nothing was sent
  if (steps.some((step) => step.ok)) return 'sent'
  // Another Exit all was already being placed from another screen (its claim, 409): the
  // rows stay held as that one, in the app's words.
  if (steps.some((step) => step.detail?.status === 409)) return 'placing'
  return 'unconfirmed' // it may have reached Upstox
}

export interface Outcomes {
  exit: SendOutcome
  cancel: SendOutcome
  /** Exit all's word on each position, by the row's id, when it gave one. */
  each: Record<string, EachAnswer> | null
}

/** How the sheet's holds settle on the answer; null is no answer at all. */
export function outcomesOf(answer: SwitchAnswer | null, sandbox: boolean): Outcomes {
  if (answer === null) return { exit: 'unanswered', cancel: 'unanswered', each: null }
  if (!answer.activated) return { exit: 'refused', cancel: 'refused', each: null }
  const steps = STEPS[sandbox ? 'sandbox' : 'live']
  const exits = named(answer, steps.exit)
  return {
    exit: outcomeOfSteps(exits),
    cancel: outcomeOfSteps(named(answer, steps.cancel)),
    each: exits.length > 0 ? exitAllEach({ positions: exits[0].detail?.positions }) : null,
  }
}

/**
 * Turn the switch on with the rows it acts on held from the tap, and settle the holds on its
 * answer. `book` is what the sheet read when it opened; null holds nothing (no book was read).
 */
export async function turnOnHeld({
  book,
  sandbox,
  stage,
  exitAll,
  send,
}: {
  book: Book | null
  sandbox: boolean
  stage: RiskStage | undefined
  exitAll: boolean
  send: () => Promise<SwitchAnswer>
}): Promise<SwitchAnswer | null> {
  const { exits, cancels } =
    book && actsOn(stage, sandbox) ? targetsOf(book) : { exits: [], cancels: [] }
  const exitBatch = exitAll && exits.length > 0 ? beginSends('exit', sandbox, exits) : null
  const cancelBatch = cancels.length > 0 ? beginSends('cancel', sandbox, cancels) : null
  let answer: SwitchAnswer | null = null
  try {
    answer = await send()
  } catch {
    answer = null // a timeout or a lost connection: it may still be running
  }
  const outcome = outcomesOf(answer, sandbox)
  if (exitBatch) settleSends(exitBatch, outcome.exit, Date.now(), null, outcome.each)
  if (cancelBatch) settleSends(cancelBatch, outcome.cancel)
  return answer
}
