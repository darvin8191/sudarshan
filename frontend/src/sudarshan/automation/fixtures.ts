// Test data for the Automation page: a strategy and a list as the server answers them.
import type { AlertLine, ListAnswer, Strategy } from './types'

export function alertLine(over: Partial<AlertLine> = {}): AlertLine {
  return {
    id: 7,
    strategy_id: 1,
    received_ms: Date.UTC(2026, 10, 16, 4, 45, 2),
    fired_s: 1_794_717_900,
    verdict: 'sent',
    sentence: 'ORB: order sent to be long 65 NIFTY 24 Nov FUT.',
    exchange: 'NFO',
    symbol: 'NIFTY24NOV26FUT',
    action: 'BUY',
    quantity: 65,
    target: 1,
    mode: 'sandbox',
    order_id: '26111600000001',
    tv_delay_ms: 1200,
    answer_ms: 310,
    ...over,
  }
}

export function strategy(over: Partial<Strategy> = {}): Strategy {
  return {
    id: 1,
    name: 'ORB',
    state: 'active',
    stopped_why: null,
    mode: 'sandbox',
    chart: 'NSE:NIFTY1!',
    kind: 'future',
    exchange: 'NFO',
    underlying: 'NIFTY',
    expiry_rule: 'month',
    strike_offset: null,
    product: 'MIS',
    direction: 'both',
    size_mode: 'fixed',
    lots: 1,
    quantity: null,
    max_age_s: 30,
    max_alerts_day: 20,
    roll_on_expiry_day: false,
    no_entry_before_stop_min: 15,
    token_tail: 'Ab3x',
    created_ms: 0,
    updated_ms: 0,
    version: 3,
    trades: 'NIFTY future, this month · Intraday · 1 lot',
    last_target: 0,
    held_exchange: null,
    held_symbol: null,
    held_side: null,
    held_since_ms: null,
    alerts_today: 0,
    last_alert: null,
    sandbox_run: { alerts: 0, sessions: 0, sent: 0, refused: 0 },
    ...over,
  }
}

export function listOf(strategies: Strategy[], over: Partial<ListAnswer> = {}): ListAnswer {
  return {
    status: 'success',
    stage: 'sandbox',
    stage_words: 'Automation is in Sandbox only.',
    live_allowed: false,
    live_blocked_why: 'Automation is in Sandbox only. Live comes in a later release.',
    kill_switch_on: false,
    notes: [],
    strategies,
    ...over,
  }
}
