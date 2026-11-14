// SUDARSHAN M12.1: how the Automation page says a contract, a moment and a verdict.
import { istTime } from '@/sudarshan/books/time'
import type { AlertLine, AutomationEvent, ListAnswer, Verdict } from './types'

const CONTRACT = /^([A-Z0-9&-]+?)(\d{2})([A-Z]{3})(\d{2})(?:FUT|(\d+(?:\.\d+)?)(CE|PE))$/

/** NIFTY24NOV26FUT as "NIFTY 24 Nov FUT"; a share's symbol as it is (the server's own rule). */
export function contractName(symbol: string): string {
  const found = CONTRACT.exec(symbol)
  if (!found) return symbol
  const month = found[3][0] + found[3].slice(1).toLowerCase()
  return `${found[1]} ${Number(found[2])} ${month} ${found[5] ? `${found[5]} ${found[6]}` : 'FUT'}`
}

const IST_DAY = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
})

/** "10:15:02" today, "12 Nov, 10:15:02" on another day, by India's clock. */
export function whenText(at: number, now: number = Date.now()): string {
  const day = IST_DAY.format(at)
  return day === IST_DAY.format(now) ? istTime(at) : `${day}, ${istTime(at)}`
}

export type Tone = 'ok' | 'quiet' | 'warn' | 'bad'

/** How a verdict reads at a glance; the sentence beside it says the rest. */
export function toneOf(verdict: Verdict): Tone {
  if (verdict === 'sent') return 'ok'
  if (verdict === 'no_action' || verdict === 'repeat' || verdict === 'claimed') return 'quiet'
  return verdict === 'not_confirmed' || verdict === 'order_refused' ? 'bad' : 'warn'
}

/** The list as the live event leaves it, until the read it sets off answers. */
export function applied(list: ListAnswer, event: AutomationEvent): ListAnswer {
  const line: AlertLine | null =
    event.kind === 'alert' && event.verdict && event.sentence
      ? {
          id: -event.at_ms,
          strategy_id: event.strategy_id,
          received_ms: event.at_ms,
          fired_s: null,
          verdict: event.verdict,
          sentence: event.sentence,
          exchange: null,
          symbol: null,
          action: null,
          quantity: null,
          target: null,
          mode: null,
          order_id: null,
          tv_delay_ms: null,
          answer_ms: null,
        }
      : null
  return {
    ...list,
    strategies: list.strategies.map((strategy) =>
      strategy.id === event.strategy_id
        ? {
            ...strategy,
            name: event.name,
            state: event.state ?? strategy.state,
            mode: event.mode ?? strategy.mode,
            last_alert: line ?? strategy.last_alert,
          }
        : strategy
    ),
  }
}
