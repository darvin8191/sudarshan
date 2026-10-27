/**
 * Market alerts as the bridge answers them (contract 1), for the Alerts page's tests. Every
 * value is made up; the words are the contract's examples.
 */
import type { AlertEvent, AlertRow, MarketAlertsState } from '@/api/marketAlerts'

/** 10:42:31 in India on Wed 28 Oct 2026. */
export const NOW = Date.UTC(2026, 9, 28, 5, 12, 31)

export function alertRow(patch: Partial<AlertRow> = {}): AlertRow {
  return {
    id: 12,
    version: 3,
    status: 'active',
    status_reason: '',
    kind: 'price',
    symbol: 'SBIN',
    exchange: 'NSE',
    instrument_key: 'NSE_EQ|INE062A01020',
    tick_size: '0.05',
    expiry: null,
    params: { op: 'crosses_above', level: '100', source: 'ltp' },
    frequency: 'every_time',
    candle_min: null,
    cooldown_s: 300,
    ends_at: Date.UTC(2026, 11, 21, 18, 30),
    note: '',
    channels: null,
    created_at: NOW - 86_400_000,
    updated_at: NOW - 86_400_000,
    words: {
      condition: 'Crosses above ₹100.00',
      meta: 'Every time · wait 5 min · ends 21 Dec',
      checks_at: null,
    },
    check: null,
    fired_today: 0,
    auction: null,
    ...patch,
  }
}

export const WATCHING = { code: 'ok', line: 'Watching · Real-time', real_time: true } as const

export function alertsState(patch: Partial<MarketAlertsState> = {}): MarketAlertsState {
  return {
    enabled: true,
    engine: WATCHING,
    segments: { NSE_EQ: WATCHING, NSE_INDEX: WATCHING, NSE_FO: WATCHING },
    limits: { instruments: 200, instruments_used: 1, active: 500, active_used: 1 },
    prefs: { telegram: true, app: true },
    alerts: [alertRow()],
    last_event_id: 340,
    server_time: NOW,
    ...patch,
  }
}

export function alertEvent(patch: Partial<AlertEvent> = {}): AlertEvent {
  return {
    id: 341,
    alert_id: 12,
    at: NOW - 5 * 60_000,
    trade_time: NOW - 5 * 60_000 - 400,
    symbol: 'SBIN',
    exchange: 'NSE',
    kind: 'price',
    title: 'SBIN crossed above ₹100.00',
    detail: 'now ₹100.05',
    note: '',
    line: 'Alert: SBIN crossed above ₹100.00: now ₹100.05 (NSE)',
    value: '₹100.05',
    level: '₹100.00',
    found_later: false,
    opening: false,
    test: false,
    deliveries: {
      telegram: { status: 'sent', reason: '', attempts: 1, updated_at: NOW - 5 * 60_000 + 1_200 },
      app: {
        status: 'sent',
        reason: 'Shown on 1 open screen',
        attempts: 1,
        updated_at: NOW - 5 * 60_000 + 100,
      },
    },
    ...patch,
  }
}
