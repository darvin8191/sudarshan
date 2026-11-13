// SUDARSHAN P6 D7b: what the service's state may say beyond the contract's `AdminState`.
//
// CONTRACT GAP, assumed here and listed in the report: the Family bot card and the install check
// line (design 6.5) read the service's /health, but the contract's `AdminState` carries neither.
// This file reads two optional fields of the state answer, `bot` and `check`, defensively: when
// the service sends neither, the card says the state is not reported yet. It never invents one.
import type { AdminState } from '../types'

export type BotState = 'polling' | 'token_missing' | 'token_refused'
const BOT_STATES: readonly BotState[] = ['polling', 'token_missing', 'token_refused']

export interface BotReport {
  state: BotState
  last_message_at: number | null
}
export interface InstallCheck {
  ok: boolean
  at: number | null
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
}

export function botOf(state: AdminState): BotReport | null {
  const bot = record(record(state)?.bot)
  const known = BOT_STATES.find((s) => s === bot?.state)
  if (!bot || !known) return null
  return {
    state: known,
    last_message_at: typeof bot.last_message_at === 'number' ? bot.last_message_at : null,
  }
}

export function checkOf(state: AdminState): InstallCheck | null {
  const check = record(record(state)?.check)
  if (!check || typeof check.ok !== 'boolean') return null
  return { ok: check.ok, at: typeof check.at === 'number' ? check.at : null }
}
