// SUDARSHAN M9: the screener's contract as the phone reads it (design 6.3 and 4.5). The bridge
// (blueprints/sudarshan_screener.py) forwards the screener service's answers as they are; every
// sentence in them is the server's and is shown as it came. Built against this file with mocked
// answers, so a change to the service's contract is a change here and nowhere else.

/** The engine line (design 5.5): `real_time` is true only while ticks arrive for the screens. */
export type EngineState =
  | 'real_time'
  | 'closed'
  | 'feed_down'
  | 'auction'
  | 'warming_up'
  | 'no_prices'
  | 'waiting_master'
  | 'switched_off'
  | 'not_answering'

export interface EngineLine {
  state: EngineState
  line: string
  real_time: boolean
  /** The next check (epoch ms), when one is due today. */
  next_at?: number | null
}

export type ScreenStatus = 'active' | 'paused' | 'stopped'
export type TimeFrame = 1 | 3 | 5 | 15 | 30 | 60 | 'D' | 'W'

// ---- the screen, as the owner writes it (design 3.2) -------------------------------------------
export type Offset = 0 | { ago: number } | 'first_today' | 'last_prev_day'

export type Operand =
  | { num: string }
  | { field: string; tf?: TimeFrame; at?: Offset }
  | { ind: string; p?: Record<string, number | string>; out?: string; tf?: TimeFrame; at?: Offset }
  | {
      oi: 'buildup' | 'oi_change_pct'
      window: { kind: 'day' } | { kind: 'candles'; n: number }
      tf?: TimeFrame
    }
  | { opt: string; underlying?: string; tf?: TimeFrame }
  | { math: 'add' | 'sub' | 'mul' | 'div'; args: [Operand, Operand] }

export interface Clause {
  id: string
  on: boolean
  left: Operand
  op: string
  right?: Operand
  args?: Record<string, string | number>
}

export type GroupKind = 'all' | 'any' | 'none'
export type Group = { all: Node[] } | { any: Node[] } | { none: Node[] }
export type Node = Clause | Group

export type Universe =
  | { kind: 'fno_stocks' | 'fno_futures' | 'index_futures' | 'indices' }
  | { kind: 'options'; underlying: string }
  | { kind: 'index_list'; id: string }
  | { kind: 'watchlist'; list_id: number }

export interface Grace {
  mode: 'runs' | 'day_end' | 'never'
  n?: number
}

export interface ScreenSpec {
  schema: 1
  name: string
  universe: Universe
  run: { on: 'close'; tf: 1 | 3 | 5 | 15 | 30 | 60 } | { on: 'after_close' }
  where: Group
  rank?: { by: Operand; order: 'asc' | 'desc' } | null
  target?: {
    list_id: number | null
    create_name: string | null
    grace: Grace
    max_adds_per_run: number
  } | null
  alerts?: AlertSettings
}

export interface AlertSettings {
  on: boolean
  min_matches: number
  unique_per_day: boolean
  skip_zero: boolean
  tell_exits: boolean
  after_trigger: 'continue' | 'pause'
  cooldown_runs: number
  max_per_day_per_symbol: number
  /** Null is the owner's usual channels; otherwise each switched on or off (the service's shape). */
  channels: { telegram: boolean; app: boolean } | null
  columns: Array<'ltp' | 'change_pct' | 'value'>
}

// ---- answers -----------------------------------------------------------------------------------
export interface ScreenRow {
  id: number
  version: number
  name: string
  status: ScreenStatus
  /** Why a Stopped screen stopped; null otherwise. */
  status_reason: string | null
  /** 'F&O stocks · 15-min', as the service words it. */
  where: string
  spec: ScreenSpec
  matches: number
  entered_today: number
  /** Instruments this screen added to its target list, and that list's name. */
  added_count: number
  target_name: string | null
  /** 'next check 10:45', or null when none is due. */
  next_check: string | null
  /** The watchlist the screen feeds, once it has one. */
  list_id?: number | null
  /** The last run's id, 0 before the first. */
  last_run_id: number
  updated_at: number
  /** 'Alerts on', 'Alerts off' or the pause notice, as the service words it (words.alerts_words). */
  alerts?: string
  /** The screen's alerts are paused after a trigger until the owner resumes them. */
  alerts_paused?: boolean
}

export interface ScreenerState {
  enabled: boolean
  engine: EngineLine
  screens: ScreenRow[]
  limits: { active: number; active_used: number; max_keys?: number }
  /** The newest run id the service holds, for the catch-up after a reconnect. */
  last_run_id: number
  server_time: number
}

/** A chip on a list row: always an icon and a word (design 4.3). */
export interface Chip {
  kind: string
  text: string
  /** A lucide icon name, as the service words it. */
  icon: string
  tone: 'brand' | 'quiet' | 'amber' | 'outline'
  /** What a screen reader says. */
  name: string
  note: string
}

export interface ResultRow {
  /** The membership row's id, for Pin and Unpin. */
  member_id: number | null
  /** The instrument key, as the feed names it. */
  key?: string
  symbol: string
  exchange: string
  rank: number
  /** At most two clause values: 'RSI 61.2 · OI +4.1%'. */
  values: string
  /** True for a value read from a forming candle. */
  forming: boolean
  chip: Chip | null
  /** The price at the run, for a row the page does not follow tick by tick. */
  ltp: number | null
  change_pct: number | null
}

export interface RunRow {
  id: number
  screen_id: number
  at: number
  /** '10:00 to 10:15'. */
  candle: string
  matches: number
  entered: number
  exited: number
  marks: string[]
  /** The service adds these; the page may ignore them. */
  missing?: number
  not_checked?: number
  status?: 'done' | 'superseded' | 'failed'
  /** Why the run did or did not raise an alert (words.told_words). */
  alerts?: string | null
}

export interface Results {
  screen_id: number
  run: RunRow | null
  rows: ResultRow[]
  not_checked: { count: number; reasons: string[] }
}

export interface RunsPage {
  runs: RunRow[]
  more: boolean
}

export interface RunSet extends Results {
  run: RunRow
  /** "Values are kept for 7 days; the list for 100 days." once the values are pruned; else null. */
  values_note?: string | null
}

export interface Preview {
  /** 'Preview on the 10:15 to 10:30 candle, not saved'. */
  label: string
  count: number
  first: ResultRow[]
  not_checked: { count: number; reasons: string[] }
  /** Candles each timeframe needs before the screen is fully ready, by timeframe. */
  needs?: Record<string, number>
}

export interface PresetCard {
  key: string
  name: string
  about: string
  usable: boolean
  where: string | null
  spec: ScreenSpec | null
  reason: string | null
}

export interface UniverseOption {
  kind: Universe['kind']
  id?: string
  list_id?: number
  underlying?: string
  label: string
  /** Why the screen can only run after the close on it, when so. */
  note: string | null
}

export interface Today {
  screens: Array<{ id: number; name: string; entered: number; exited: number; matches: number }>
  recent: Array<{
    symbol: string
    exchange: string
    kind: 'entered' | 'exited'
    at: number
    screen: string
  }>
  unseen_alerts: number
  /** The trading day the figures are for (YYYYMMDD). */
  session?: number
}

// ---- the dashboard (M9.16): a widget is addressed by its screen and its kind, never by an id ----
export type WidgetKind = 'table' | 'count'
export type CheckMode = 'intraday' | 'forming_day' | 'after_close'

export interface WidgetSort {
  by: 'change_pct' | 'ltp' | 'entered' | 'value'
  order: 'asc' | 'desc'
  /** Only with `by: 'value'`. */
  clause?: string
}

/** What a card's line is, for the icon; `words` is the server's sentence, shown as it came. */
export type CardLineKind =
  | 'stopped'
  | 'paused'
  | 'engine'
  | 'waiting'
  | 'waits'
  | 'closed'
  | 'feed_down'
  | 'after_close'
  | 'partial'
  | 'real_time'

export interface CardLine {
  kind: CardLineKind
  /** For a card that is not live this is the engine line's own sentence (why it is not). */
  words: string
  /** True only while checks and ticks arrive: the page may override it, never the other way. */
  live: boolean
  state: EngineState
}

export interface WidgetCard {
  screen_id: number
  kind: WidgetKind
  position: number
  rows: 5 | 10 | 20
  /** As used: null when the widget sorts by a line the screen no longer has. */
  sort_by: WidgetSort | null
  value_clauses: string[] | null
  name: string
  status: ScreenStatus
  status_reason: string | null
  /** 'F&O stocks · 5-min'. */
  where: string
  mode: CheckMode
  /** 'Forming day candle · each 15-min close', 'After the close (16:05)', '5-min closes'. */
  mode_words: string
  /** The screen reads only day candles and session levels, so "when it checks" is the owner's. */
  day_only: boolean
  /** Why the screen cannot judge yet, or null. */
  waits: string | null
  /** The screen's next check (epoch ms). */
  next_at: number | null
  line: CardLine
  /**
   * True for a card the dashboard read did not have time to work out: it carries its identity, its
   * screen's name and state and no rows, and the page reads it with the widget read. The facts
   * below are then null or empty.
   */
  rows_pending: boolean
  run: RunRow | null
  matches: number | null
  /** A table: at most `rows`, in the widget's order, ranks renumbered to it. */
  rows_shown: ResultRow[]
  /** Matches past `rows`. */
  more: number
  not_checked: { count: number; reasons: string[] } | null
  /** Its automatic watchlist. */
  list: { id: number | null; name: string | null; count: number } | null
  /** A count card only. */
  counts?: {
    entered_today: number
    exited_today: number
    series: Array<{ at: number; matches: number }>
    series_words: string
  }
}

export interface Dashboard {
  /** 0 while the layout is only a suggestion (one table for each Active screen). */
  version: number
  name: string
  suggested: boolean
  widgets: WidgetCard[]
  limits: { widgets: number }
  last_run_id: number
  server_time: number
}

// ---- pushes (design 4.5) ------------------------------------------------------------------------
export interface RunPush {
  screen_id: number
  run_id: number
  run_key: string
  at: number
  candle: string
  counts: { matches: number; entered: number; exited: number; missing: number; not_checked: number }
  entered: ResultRow[]
  exited: Array<{ symbol: string; exchange: string }>
  marks: string[]
}

export interface StatePush {
  engine: EngineLine
  screens: Array<
    Pick<
      ScreenRow,
      'id' | 'version' | 'status' | 'status_reason' | 'next_check' | 'matches' | 'entered_today'
    >
  >
  removed: number[]
  resync?: true
}

/** The sentences the page shows when the server sent none. */
export const WORDS = {
  unanswered: 'No answer came from OpenAlgo. Check the connection, then try again.',
  not_confirmed: 'Not confirmed: open the screen again to see whether it was saved.',
  unsent: 'The request could not be sent. Reload the page, then try again.',
  unreadable: 'OpenAlgo’s answer could not be read. Try again in a minute.',
  session_ended: 'Your session has ended. Log in again.',
  conflict: 'This screen was changed on another screen. Open it again, then edit it.',
  not_answering:
    'The screener is not answering. Your screens are kept; checking resumes when it is back.',
  switched_off: 'The screener is switched off on the server.',
  gone: 'This screen was deleted.',
} as const

export type IndOperand = Extract<Operand, { ind: string }>
export type OptOperand = Extract<Operand, { opt: string }>
export type OiOperand = Extract<Operand, { oi: 'buildup' | 'oi_change_pct' }>
export type FieldOperand = Extract<Operand, { field: string }>
