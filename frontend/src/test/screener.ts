// Fixtures for the Screener's tests: screens, states and a stand-in for the shared connection.
import type {
  Dashboard,
  EngineLine,
  ResultRow,
  RunRow,
  ScreenerState,
  ScreenRow,
  ScreenSpec,
  WidgetCard,
} from '@/sudarshan/screener/types'

export const NOW = Date.UTC(2026, 10, 9, 5, 15, 0)

export const SPEC: ScreenSpec = {
  schema: 1,
  name: '15-min EMA cross',
  universe: { kind: 'fno_stocks' },
  run: { on: 'close', tf: 15 },
  where: {
    all: [
      {
        id: 'c1',
        on: true,
        left: { ind: 'ema', p: { length: 20 }, tf: 15 },
        op: 'crosses_above',
        right: { ind: 'ema', p: { length: 50 }, tf: 15 },
      },
      {
        id: 'c2',
        on: true,
        left: { ind: 'rsi', p: { length: 14 }, tf: 15 },
        op: 'gt',
        right: { num: '50' },
      },
    ],
  },
}

export function screenRow(over: Partial<ScreenRow> = {}): ScreenRow {
  return {
    id: 1,
    version: 1,
    name: '15-min EMA cross',
    status: 'active',
    status_reason: null,
    where: 'F&O stocks · 15-min',
    spec: SPEC,
    matches: 7,
    entered_today: 3,
    added_count: 7,
    target_name: 'Screener: 15-min EMA cross',
    next_check: 'next check 10:45',
    last_run_id: 10,
    updated_at: NOW,
    ...over,
  }
}

export const REAL_TIME: EngineLine = {
  state: 'real_time',
  line: 'Real-time · next check 10:45',
  real_time: true,
}

export function screenerState(
  screens: ScreenRow[] = [screenRow()],
  over: Partial<ScreenerState> = {}
): ScreenerState {
  return {
    enabled: true,
    engine: REAL_TIME,
    screens,
    limits: { active: 12, active_used: screens.filter((row) => row.status === 'active').length },
    last_run_id: 10,
    server_time: NOW,
    ...over,
  }
}

export function resultRow(symbol: string, over: Partial<ResultRow> = {}): ResultRow {
  return {
    member_id: null,
    symbol,
    exchange: 'NSE',
    rank: 1,
    values: 'RSI 61.2',
    forming: false,
    chip: null,
    ltp: 100,
    change_pct: 1.2,
    ...over,
  }
}

export function runRow(id: number, over: Partial<RunRow> = {}): RunRow {
  return {
    id,
    screen_id: 1,
    at: NOW,
    candle: '10:00 to 10:15',
    matches: 7,
    entered: 2,
    exited: 1,
    marks: [],
    ...over,
  }
}

type Handler = (...args: any[]) => void

/** The shared connection as the page sees it: events fired by hand, emits recorded. */
export function fakeSocket(connected = true) {
  const handlers = new Map<string, Set<Handler>>()
  const emits: Array<{ event: string; ack: Handler }> = []
  const socket = {
    connected,
    on(event: string, handler: Handler) {
      if (!handlers.has(event)) handlers.set(event, new Set())
      handlers.get(event)?.add(handler)
      return socket
    },
    off(event: string, handler: Handler) {
      handlers.get(event)?.delete(handler)
      return socket
    },
    timeout: () => ({
      emit: (event: string, ack: Handler) => {
        emits.push({ event, ack })
      },
    }),
    listeners: (event: string) => [...(handlers.get(event) ?? [])],
  }
  return {
    socket,
    emits,
    fire(event: string, ...args: unknown[]) {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
    count: (event: string) => handlers.get(event)?.size ?? 0,
  }
}

/** One dashboard card as the `dashboard` and `widget` ops answer it (a table of two rows). */
export function widgetCard(over: Partial<WidgetCard> = {}): WidgetCard {
  const screenId = over.screen_id ?? 1
  return {
    screen_id: screenId,
    kind: 'table',
    position: 0,
    rows: 10,
    sort_by: null,
    value_clauses: null,
    name: '15-min EMA cross',
    status: 'active',
    status_reason: null,
    where: 'F&O stocks · 15-min',
    mode: 'intraday',
    mode_words: '15-min closes',
    day_only: false,
    waits: null,
    next_at: NOW + 1_800_000,
    line: {
      kind: 'real_time',
      words: 'Real-time · next check 10:45',
      live: true,
      state: 'real_time',
    },
    rows_pending: false,
    run: runRow(10, { screen_id: screenId }),
    matches: 2,
    rows_shown: [resultRow('INFY', { rank: 1 }), resultRow('SBIN', { rank: 2, change_pct: -0.6 })],
    more: 0,
    not_checked: { count: 0, reasons: [] },
    list: { id: 5, name: 'Screener: 15-min EMA cross', count: 2 },
    not_live_words: null,
    ...over,
  }
}

export function dashboard(widgets: WidgetCard[] = [widgetCard()], over: Partial<Dashboard> = {}) {
  return {
    version: 0,
    name: 'Dashboard',
    suggested: true,
    widgets: widgets.map((card, position) => ({ ...card, position })),
    limits: { widgets: 20 },
    last_run_id: 10,
    server_time: NOW,
    ...over,
  } satisfies Dashboard
}
