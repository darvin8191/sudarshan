/**
 * The portfolio's contract on the phone (M7.19, M7.20): units, kinds, op names and the JSON each
 * op sends and answers.
 *
 * Its twin on the server is sudarshan/portfolio/core/kinds.py, and
 * sudarshan/portfolio/tests/test_portfolio_contract.py holds the two together: every number,
 * list, label, sentence and op below is compared with the server's, so a change on one side that
 * the other does not follow fails the build. The prose is in sudarshan/portfolio/README.md.
 *
 * Units, at every service boundary (cross-design X3):
 *
 *   money        integer paise                           `*_paise`
 *   quantity     integer qty_u = units x 10^6            `qty_u`
 *   price        integer price_u = rupees x 10^4         `price_u`
 *   percentage   Decimal string, in percent, 4 decimals  `*_pct`, "8.0000" or "-2.5000"
 *   instant      epoch milliseconds                      `*_at`
 *   day          integer YYYYMMDD in IST                 `day`, `trade_date`, `lot_date`
 *
 * qty_u and price_u fit a browser number (below 2^53), but their product does not: the value of
 * a holding line is round(qty_u x price_u / 10^8) paise, half away from zero, and is computed
 * with BigInt (valuation.ts). Percentages are never floats on the wire.
 */

/** Integer paise. */
export type Paise = number
/** Integer quantity, units x 10^6 (six decimals: fund units, grams, whole shares). */
export type QtyU = number
/** Integer price, rupees x 10^4 (four decimals: NAVs, currency). */
export type PriceU = number
/** A percentage in percent as a Decimal string with four decimals: "8.0000", "-2.5000". */
export type Pct = string
/** An IST calendar day as the integer YYYYMMDD (20261108), a trading day or not. */
export type Day = number
/** Epoch milliseconds. */
export type EpochMs = number

// ---- numbers: the same values as core/kinds.py ----
export const PAISE_PER_RUPEE = 100
export const QTY_SCALE = 1_000_000
export const PRICE_SCALE = 10_000
export const VALUE_DIVISOR = 100_000_000
export const PCT_DECIMALS = 4
export const MAX_UNITS = 1_000_000_000
export const MAX_QTY_U = 1_000_000_000_000_000
export const MAX_PRICE_U = 10_000_000_000_000
export const MAX_PAISE = 1_000_000_000_000_000
export const MAX_TABS = 50
export const TAB_NAME_MAX = 40
export const NAME_MAX = 80
export const NOTE_MAX = 500
export const TAGS_MAX = 10
export const TAG_MAX = 30
export const MANUAL_OLD_DAYS = 30
export const MANUAL_OLD_DAYS_MIN = 7
export const MANUAL_OLD_DAYS_MAX = 365
export const ACTIVITIES_PAGE = 100
export const ACTIVITIES_PAGE_MAX = 500
export const MF_SEARCH_MAX = 20
export const IMPORT_PREVIEW_ROWS = 500
export const UPSTOX_HISTORY_DAYS = 400
export const REPORT_YEARS = 3
export const NAV_OLD_BUSINESS_DAYS = 3
export const XIRR_MIN_DAYS = 365
export const IMPORT_MAX_FILE_BYTES = 900_000
export const IMPORT_MAX_ROWS = 5_000
export const LAYOUT_VERSION = 1
export const GOALS_MAX = 20
export const STAT_DAYS = 60
export const STAT_MIN_DAYS = 20
export const CALENDAR_PAST_DAYS = 90
export const CALENDAR_NEXT_DAYS = 180

// ---- lists: the same members, in the same order, as core/kinds.py ----
export const ACTIVITY_KINDS = [
  'BUY',
  'SELL',
  'DIVIDEND',
  'INTEREST',
  'FEE',
  'TRANSFER',
  'SPLIT',
  'BONUS',
  'RIGHTS',
] as const
export type ActivityKind = (typeof ACTIVITY_KINDS)[number]
export const ACTIVITY_STATUSES = ['posted', 'draft'] as const
export type ActivityStatus = (typeof ACTIVITY_STATUSES)[number]
export const ACTIVITY_SOURCES = ['manual', 'import'] as const
export type ActivitySource = (typeof ACTIVITY_SOURCES)[number]
export const CHARGE_PARTS = [
  'brokerage',
  'stt',
  'exchange',
  'sebi',
  'stamp',
  'gst',
  'dp',
  'other',
] as const
export type ChargePart = (typeof CHARGE_PARTS)[number]
export const ASSET_KINDS = ['listed', 'mf', 'manual', 'liability'] as const
export type AssetKind = (typeof ASSET_KINDS)[number]
export const HOLDING_KINDS = ['listed', 'mf', 'manual'] as const
export type HoldingKind = (typeof HOLDING_KINDS)[number]
export const MCAPS = ['large', 'mid', 'small'] as const
export type Mcap = (typeof MCAPS)[number]
export const ACCOUNT_KINDS = ['demat', 'bank', 'mf_folio', 'physical', 'upstox', 'other'] as const
export type AccountKind = (typeof ACCOUNT_KINDS)[number]
export const TAB_KINDS = [
  'upstox',
  'stocks',
  'mf',
  'mixed',
  'gold',
  'cash',
  'real_estate',
  'esop',
  'crypto',
  'other',
] as const
export type TabKind = (typeof TAB_KINDS)[number]
export const TAB_ICONS = [
  'briefcase',
  'chart',
  'landmark',
  'coins',
  'home',
  'gem',
  'wallet',
  'target',
] as const
export type TabIcon = (typeof TAB_ICONS)[number]
export const TAB_COLOURS = ['brand', 'gold', 'info', 'neutral'] as const
export type TabColour = (typeof TAB_COLOURS)[number]
export const ASSET_CLASSES = [
  'equity',
  'mf_equity',
  'mf_debt',
  'mf_hybrid',
  'mf_other',
  'gold',
  'cash',
  'bonds',
  'real_estate',
  'esop',
  'crypto',
  'other',
] as const
export type AssetClass = (typeof ASSET_CLASSES)[number]
export const PRICE_SOURCES = ['feed', 'quote', 'amfi', 'manual'] as const
export type PriceSource = (typeof PRICE_SOURCES)[number]
export const PRICE_BASES = ['last', 'close', 'nav', 'manual'] as const
export type PriceBasis = (typeof PRICE_BASES)[number]
export const IMPORT_FORMATS = ['generic', 'zerodha'] as const
export type ImportFormat = (typeof IMPORT_FORMATS)[number]
export const IMPORT_STATUSES = ['preview', 'committed', 'discarded', 'undone'] as const
export type ImportStatus = (typeof IMPORT_STATUSES)[number]
export const IMPORT_ACTIONS = [
  'BUY',
  'SELL',
  'DIVIDEND',
  'INTEREST',
  'FEE',
  'SPLIT',
  'BONUS',
  'RIGHTS',
  'TRANSFER_IN',
  'TRANSFER_OUT',
  'TRANSFER',
] as const
export type ImportAction = (typeof IMPORT_ACTIONS)[number]
export const IMPORT_ROW_STATUSES = ['new', 'dup', 'bad'] as const
export type ImportRowStatus = (typeof IMPORT_ROW_STATUSES)[number]
export const TODAY_STATES = ['ok', 'unknown', 'pending', 'none'] as const
export type TodayState = (typeof TODAY_STATES)[number]
export const TODAY_MODES = ['ok', 'pending', 'none'] as const
export type TodayMode = (typeof TODAY_MODES)[number]
export const FRESHNESS_STATES = ['fresh', 'old'] as const
export type FreshnessState = (typeof FRESHNESS_STATES)[number]
export const XIRR_STATES = ['shown', 'later', 'none'] as const
export type XirrState = (typeof XIRR_STATES)[number]
export const BENCHMARKS = ['nifty50', 'nifty500'] as const
export type Benchmark = (typeof BENCHMARKS)[number]
export const JOB_NAMES = [
  'eod',
  'settle',
  'nav_fetch',
  'nav_settle',
  'benchmark',
  'resolve',
  'reports',
  'corporate_actions',
] as const
export type JobName = (typeof JOB_NAMES)[number]
export const JOB_STATUSES = ['ok', 'late', 'failed', 'skipped'] as const
export type JobStatus = (typeof JOB_STATUSES)[number]
export const WIDGETS = [
  'summary',
  'today',
  'holdings',
  'movers',
  'allocation',
  'history',
  'performance',
  'income',
  'notes',
] as const
export type Widget = (typeof WIDGETS)[number]
export const HOLDING_COLUMNS = [
  'name',
  'quantity',
  'avg_cost',
  'price',
  'today',
  'value',
  'gain',
  'weight',
] as const
export type HoldingColumn = (typeof HOLDING_COLUMNS)[number]
export const SORTS = ['value', 'today_pct', 'gain_pct', 'weight', 'name'] as const
export type Sort = (typeof SORTS)[number]
export const GROUPS = ['none', 'account', 'class', 'sector', 'tag'] as const
export type Group = (typeof GROUPS)[number]
export const ALLOCATION_BY = ['class', 'sector', 'mcap', 'account', 'member', 'tag'] as const
export type AllocationBy = (typeof ALLOCATION_BY)[number]
export const HELD_FILTERS = ['over', 'under'] as const
export type HeldFilter = (typeof HELD_FILTERS)[number]
export const UPSTOX_PARTS = ['holdings', 'positions', 'mtf', 'funds', 'orders', 'trades'] as const
export type UpstoxPartName = (typeof UPSTOX_PARTS)[number]
export const REPORT_SEGMENTS = ['EQ', 'FO', 'COM', 'CD', 'MF'] as const
export type ReportSegment = (typeof REPORT_SEGMENTS)[number]
export const REPORT_KINDS = ['trades', 'pnl'] as const
export type ReportKind = (typeof REPORT_KINDS)[number]
export const REPORT_STATES = ['ok', 'partial', 'reading', 'unavailable'] as const
export type ReportState = (typeof REPORT_STATES)[number]
export const PART_STATES = ['ok', 'stale', 'unavailable'] as const
export type PartState = (typeof PART_STATES)[number]
export const ERROR_CODES = [
  'not_ready',
  'bad_request',
  'not_found',
  'version_conflict',
  'refused',
  'unavailable',
  'not_confirmed',
  'unauthorized',
  'forbidden',
  'rate_limited',
] as const
export type ErrorCode = (typeof ERROR_CODES)[number]
export const QUERY_TYPES = ['int', 'day', 'str', 'ints', 'strs', 'bool'] as const
export type QueryType = (typeof QUERY_TYPES)[number]
/** A tab share's level: `view` shows what the member's privacy allows, `restricted` no figures. */
export const SHARE_LEVELS = ['view', 'restricted'] as const
export type ShareLevel = (typeof SHARE_LEVELS)[number]
/** The four categories of a family member's privacy (sudarshan/family/privacy_fields.json). */
export const PRIVACY_CATEGORIES = ['pnl', 'qty', 'funds', 'orders'] as const
export type PrivacyCategory = (typeof PRIVACY_CATEGORIES)[number]
// The money features of phase P7: tax, insights, goals (the buckets, methods and readings are the
// tax core's, sudarshan/tax).
export const TAX_INPUT_KINDS = [
  'opening_lot',
  'fmv',
  'carry',
  'day_override',
  'trade_mark',
  'settings',
] as const
export type TaxInputKind = (typeof TAX_INPUT_KINDS)[number]
export const TAX_MODES = ['reports', 'ledger', 'none'] as const
export type TaxMode = (typeof TAX_MODES)[number]
export const TAX_SCHEDULES = ['capital_gains', 'business', 'losses', 'dividends'] as const
export type TaxSchedule = (typeof TAX_SCHEDULES)[number]
export const CARRY_BUCKETS = ['stcl', 'ltcl', 'business', 'speculative'] as const
export type CarryBucket = (typeof CARRY_BUCKETS)[number]
export const TURNOVER_METHODS = ['abs_pnl', 'abs_pnl_plus_sale_premium'] as const
export type TurnoverMethod = (typeof TURNOVER_METHODS)[number]
export const EXEMPT_ORDERS = ['highest_rate_first', 'chronological'] as const
export type ExemptOrder = (typeof EXEMPT_ORDERS)[number]
export const LT_BOUNDARIES = ['after_anniversary', 'on_anniversary'] as const
export type LtBoundary = (typeof LT_BOUNDARIES)[number]
export const CA_KINDS = ['dividend', 'bonus', 'split', 'rights'] as const
export type CaKind = (typeof CA_KINDS)[number]
export const CA_DECISIONS = ['applied', 'dismissed'] as const
export type CaDecision = (typeof CA_DECISIONS)[number]
export const DIVIDEND_STATES = ['expected', 'confirmed', 'not_received'] as const
export type DividendState = (typeof DIVIDEND_STATES)[number]
export const EXPORT_VIEWS = ['holdings', 'activity', 'performance', 'allocation', 'income'] as const
export type ExportView = (typeof EXPORT_VIEWS)[number]
export const SETTING_KEYS = [
  'risk_free_bp',
  'max_holding_pct',
  'max_sector_pct',
  'monthly_expenses_paise',
  'months_target',
] as const
export type SettingKey = (typeof SETTING_KEYS)[number]
/** The phase's switches, each off on the server until a release turns it on. */
export const SWITCH_NAMES = ['tax', 'insights', 'goals'] as const
export type SwitchName = (typeof SWITCH_NAMES)[number]

// ---- strings ----
export const API_PREFIX = '/sudarshan/portfolio/api'

// ---- labels and sentences ----
export const ACTIVITY_KIND_LABELS: Record<ActivityKind, string> = {
  BUY: 'Buy',
  SELL: 'Sell',
  DIVIDEND: 'Dividend',
  INTEREST: 'Interest',
  FEE: 'Fee',
  TRANSFER: 'Transfer',
  SPLIT: 'Split',
  BONUS: 'Bonus',
  RIGHTS: 'Rights',
}
export const ACCOUNT_KIND_LABELS: Record<AccountKind, string> = {
  demat: 'Demat account',
  bank: 'Bank',
  mf_folio: 'Mutual fund folio',
  physical: 'Held physically',
  upstox: 'Upstox',
  other: 'Other',
}
export const TAB_KIND_LABELS: Record<TabKind, string> = {
  upstox: 'Upstox',
  stocks: 'Stocks and ETFs',
  mf: 'Mutual funds',
  mixed: 'Mixed',
  gold: 'Gold and silver',
  cash: 'FD and cash',
  real_estate: 'Real estate',
  esop: 'ESOP and unlisted',
  crypto: 'Crypto',
  other: 'Other',
}
export const ASSET_CLASS_LABELS: Record<AssetClass, string> = {
  equity: 'Equity',
  mf_equity: 'MF equity',
  mf_debt: 'MF debt',
  mf_hybrid: 'MF hybrid',
  mf_other: 'MF other',
  gold: 'Gold and silver',
  cash: 'FD and cash',
  bonds: 'Bonds',
  real_estate: 'Real estate',
  esop: 'ESOP and unlisted',
  crypto: 'Crypto',
  other: 'Other',
}
export const BENCHMARK_LABELS: Record<Benchmark, string> = {
  nifty50: 'NIFTY 50',
  nifty500: 'NIFTY 500',
}
export const WORDS = {
  off: 'The portfolio is switched off on the server.',
  not_ready: 'This part of the portfolio is not ready yet.',
  not_answering:
    'The portfolio service is not answering. Your entries are safe; try again in a minute.',
  not_confirmed: 'Not confirmed: open the tab to see whether it was saved.',
  version_conflict:
    'This tab was changed on another screen. Nothing was saved; open it again and repeat the change.',
  version_conflict_item:
    'This item was changed on another screen. Nothing was saved; open it again and repeat the change.',
  store_failed:
    'Your portfolio data could not be opened. Nothing was changed; check the service on the server.',
  store_newer:
    'Your portfolio data was written by a newer release. Install that release again; nothing was changed.',
  future_date: 'A date after today can be saved as a draft only.',
  tab_limit: 'A portfolio can have up to 50 tabs. Archive one you no longer use.',
  import_too_big:
    'This file is larger than 900 KB. Download it in shorter date ranges and import each part.',
  whole_shares: 'Shares are whole numbers.',
  forbidden: 'Only the account owner can use the portfolio.',
  unauthorized: 'Your session has ended. Log in again.',
  rate_limited: 'Too many requests. Wait a minute, then try again.',
  tax_reading:
    "The tax working paper needs Upstox's trade history for this year. It is being read; this takes a moment.",
  tax_years: 'Upstox keeps the last three financial years only.',
} as const
export type WordKey = keyof typeof WORDS

// ---- the ops: the same table as core/kinds.py OPS ----
export const OPS: readonly OpSpec[] = [
  { name: 'state', method: 'GET', route: '/state', cls: 'read' },
  { name: 'summary', method: 'GET', route: '/summary', cls: 'read' },
  { name: 'tab_create', method: 'POST', route: '/tabs', cls: 'write' },
  {
    name: 'tab_get',
    method: 'GET',
    route: '/tabs/<id>',
    cls: 'read',
    query: [
      ['as_of', 'day'],
      ['view', 'int'],
      ['accounts', 'ints'],
      ['classes', 'strs'],
      ['tags', 'strs'],
      ['held', 'str'],
    ],
  },
  { name: 'tab_update', method: 'PATCH', route: '/tabs/<id>', cls: 'write' },
  { name: 'tab_delete', method: 'DELETE', route: '/tabs/<id>', cls: 'write' },
  {
    name: 'activities_list',
    method: 'GET',
    route: '/tabs/<id>/activities',
    cls: 'read',
    query: [
      ['before', 'str'],
      ['limit', 'int'],
      ['asset', 'int'],
    ],
  },
  { name: 'activity_create', method: 'POST', route: '/tabs/<id>/activities', cls: 'write' },
  { name: 'activity_update', method: 'PATCH', route: '/activities/<id>', cls: 'write' },
  { name: 'activity_delete', method: 'DELETE', route: '/activities/<id>', cls: 'write' },
  { name: 'asset_create', method: 'POST', route: '/assets', cls: 'write' },
  { name: 'asset_update', method: 'PATCH', route: '/assets/<id>', cls: 'write' },
  { name: 'price_point_set', method: 'POST', route: '/assets/<id>/prices', cls: 'write' },
  { name: 'loan_move', method: 'POST', route: '/assets/<id>/tab', cls: 'write' },
  {
    name: 'mf_search',
    method: 'GET',
    route: '/mf/search',
    cls: 'read',
    query: [
      ['q', 'str'],
      ['limit', 'int'],
    ],
  },
  { name: 'account_create', method: 'POST', route: '/accounts', cls: 'write' },
  { name: 'account_update', method: 'PATCH', route: '/accounts/<id>', cls: 'write' },
  { name: 'member_create', method: 'POST', route: '/members', cls: 'write' },
  { name: 'member_update', method: 'PATCH', route: '/members/<id>', cls: 'write' },
  {
    name: 'import_preview',
    method: 'POST',
    route: '/tabs/<id>/imports',
    cls: 'import',
    upload: true,
  },
  { name: 'import_commit', method: 'POST', route: '/imports/<id>/commit', cls: 'write' },
  { name: 'import_discard', method: 'DELETE', route: '/imports/<id>', cls: 'write' },
  {
    name: 'export_csv',
    method: 'GET',
    route: '/tabs/<id>/export.csv',
    cls: 'read',
    query: [['view', 'str']],
    download: true,
  },
  { name: 'performance', method: 'GET', route: '/tabs/<id>/performance', cls: 'read' },
  { name: 'layout_get', method: 'GET', route: '/tabs/<id>/layout', cls: 'read' },
  { name: 'layout_set', method: 'PUT', route: '/tabs/<id>/layout', cls: 'write' },
  { name: 'view_list', method: 'GET', route: '/tabs/<id>/views', cls: 'read' },
  { name: 'view_create', method: 'POST', route: '/tabs/<id>/views', cls: 'write' },
  { name: 'view_update', method: 'PATCH', route: '/views/<id>', cls: 'write' },
  { name: 'view_delete', method: 'DELETE', route: '/views/<id>', cls: 'write' },
  {
    name: 'upstox_book',
    method: 'GET',
    route: '/upstox',
    cls: 'upstox',
    query: [
      ['parts', 'strs'],
      ['max_age', 'int'],
    ],
  },
  {
    name: 'upstox_history',
    method: 'GET',
    route: '/upstox/history',
    cls: 'upstox',
    query: [['limit', 'int']],
  },
  { name: 'report_years', method: 'GET', route: '/reports/years', cls: 'upstox' },
  {
    name: 'report_trades',
    method: 'GET',
    route: '/reports/trades',
    cls: 'upstox',
    query: [
      ['fy', 'int'],
      ['segment', 'str'],
      ['force', 'bool'],
    ],
  },
  {
    name: 'report_pnl',
    method: 'GET',
    route: '/reports/pnl',
    cls: 'upstox',
    query: [
      ['fy', 'int'],
      ['segment', 'str'],
      ['force', 'bool'],
    ],
  },
  {
    name: 'shares_list',
    method: 'GET',
    route: '/family/shares',
    cls: 'read',
    query: [
      ['tab_id', 'int'],
      ['viewer', 'str'],
    ],
  },
  { name: 'share_set', method: 'PUT', route: '/family/shares', cls: 'write' },
  { name: 'share_remove', method: 'DELETE', route: '/family/shares', cls: 'write' },
  { name: 'member_privacy_set', method: 'PUT', route: '/family/privacy', cls: 'write' },
  // Phase P7: each answers "not ready" until its part lands and while its switch is off.
  {
    name: 'tax_get',
    method: 'GET',
    route: '/tax',
    cls: 'read',
    query: [
      ['fy', 'int'],
      ['member', 'int'],
    ],
  },
  {
    name: 'tax_inputs',
    method: 'GET',
    route: '/tax/inputs',
    cls: 'read',
    query: [['member', 'int']],
  },
  { name: 'tax_input_set', method: 'POST', route: '/tax/inputs', cls: 'write' },
  { name: 'tax_input_delete', method: 'DELETE', route: '/tax/inputs/<id>', cls: 'write' },
  {
    name: 'tax_csv',
    method: 'GET',
    route: '/tax/export.csv',
    cls: 'read',
    query: [
      ['fy', 'int'],
      ['member', 'int'],
      ['schedule', 'str'],
    ],
    download: true,
  },
  {
    name: 'report_csv',
    method: 'GET',
    route: '/reports/export.csv',
    cls: 'read',
    query: [
      ['kind', 'str'],
      ['fy', 'int'],
      ['segment', 'str'],
    ],
    download: true,
  },
  {
    name: 'ca_list',
    method: 'GET',
    route: '/corporate-actions',
    cls: 'read',
    query: [
      ['scope', 'str'],
      ['from', 'day'],
      ['to', 'day'],
    ],
  },
  { name: 'ca_decide', method: 'POST', route: '/corporate-actions/<id>', cls: 'write' },
  {
    name: 'dividends',
    method: 'GET',
    route: '/dividends',
    cls: 'read',
    query: [
      ['fy', 'int'],
      ['member', 'int'],
    ],
  },
  { name: 'dividend_set', method: 'POST', route: '/dividends/<id>', cls: 'write' },
  {
    name: 'calendar',
    method: 'GET',
    route: '/calendar',
    cls: 'read',
    query: [
      ['from', 'day'],
      ['to', 'day'],
    ],
  },
  { name: 'risk', method: 'GET', route: '/risk', cls: 'read', query: [['tab', 'int']] },
  { name: 'settings_get', method: 'GET', route: '/settings', cls: 'read' },
  { name: 'settings_set', method: 'PUT', route: '/settings', cls: 'write' },
  { name: 'goals_list', method: 'GET', route: '/goals', cls: 'read' },
  { name: 'goal_create', method: 'POST', route: '/goals', cls: 'write' },
  { name: 'goal_update', method: 'PATCH', route: '/goals/<id>', cls: 'write' },
  {
    name: 'export_pdf',
    method: 'GET',
    route: '/tabs/<id>/export.pdf',
    cls: 'read',
    query: [['view', 'str']],
    download: true,
  },
]

// ---- the answer every op gives ----

export interface PortfolioError {
  code: ErrorCode
  /** A sentence for the owner. Never a status code, a class name or a protocol term. */
  message: string
  /** `refused`: the entry that stands in the way, when there is one. */
  entry_id?: number | null
}

/**
 * Success carries the op's data. A switched-off portfolio is a success with `enabled: false`
 * and no data (the screen says WORDS.off). An error carries a code and a sentence; `enabled` is
 * missing when the bridge made the error without asking the service.
 */
export type PortfolioAnswer<T> =
  | { status: 'success'; enabled: true; data: T }
  | { status: 'success'; enabled: false; data: null }
  | { status: 'error'; enabled?: boolean; error: PortfolioError }

// ---- people, places and things ----

export interface Member {
  id: number
  name: string
  relation: string | null
  version: number
}

export interface Account {
  id: number
  member_id: number
  name: string
  kind: AccountKind
  broker: string | null
  note: string | null
  /** The Upstox account of the Upstox tab: made on first start, never edited or removed. */
  system: boolean
  version: number
}

export interface TabInfo {
  id: number
  name: string
  /** An optional second name in Devanagari or Gujarati. */
  name_alt: string | null
  kind: TabKind
  icon: TabIcon
  colour: TabColour
  position: number
  pinned: boolean
  archived: boolean
  /** The Upstox tab: first, read-only, never archived or deleted. */
  system: boolean
  /** A manual value or a NAV older than this many days is marked Old (7 to 365). */
  manual_old_days: number
  version: number
}

export interface AssetAlias {
  kind: 'isin' | 'symbol'
  value: string
}

export interface Asset {
  id: number
  kind: AssetKind
  name: string
  /** Listed: the exchange and symbol in OpenAlgo's words (the screen subscribes with them). */
  exchange: string | null
  symbol: string | null
  isin: string | null
  /** Mutual fund: AMFI's scheme code. */
  amfi_code: string | null
  /** Manual: what one unit is ("g", "sq ft"). */
  unit_label: string | null
  asset_class: AssetClass
  sector: string | null
  mcap: Mcap | null
  tags: string[]
  aliases: AssetAlias[]
  tick_size_u: PriceU | null
  resolved_at: EpochMs | null
  version: number
}

/** What a holding row says about its asset. */
export type AssetRef = Pick<
  Asset,
  | 'id'
  | 'kind'
  | 'name'
  | 'exchange'
  | 'symbol'
  | 'isin'
  | 'unit_label'
  | 'asset_class'
  | 'sector'
  | 'mcap'
  | 'tags'
>

export interface PricePoint {
  asset_id: number
  day: Day
  price_u: PriceU
}

// ---- entries ----

export type ChargesDetail = Partial<Record<`${ChargePart}_paise`, Paise>>

/**
 * What the owner enters, and what an edit replaces. Fields by kind (README, "Fields by kind"):
 * BUY and SELL and RIGHTS take an account, an asset, a quantity and an amount; DIVIDEND and
 * INTEREST an account and an amount; FEE an account and an amount; TRANSFER an asset, a quantity
 * and the account it leaves (`account_id`, null: from outside) and the account it reaches
 * (`to_account_id`, null: to outside); SPLIT an asset and a ratio; BONUS an account, an asset and
 * the shares received.
 */
export interface ActivityInput {
  kind: ActivityKind
  /** A draft counts nowhere. A date after today can be a draft only. Default posted. */
  status?: ActivityStatus
  trade_date: Day
  /** The account; TRANSFER: the account it leaves (null: from outside); SPLIT: null is every account. */
  account_id: number | null
  to_account_id?: number | null
  asset_id?: number | null
  qty_u?: QtyU | null
  /** Informational: the amount is what counts. */
  price_u?: PriceU | null
  /** The gross value of a trade; a dividend's or interest's gross; a fee; a lot's cost on a transfer in. */
  amount_paise?: Paise | null
  charges_paise?: Paise
  charges_detail?: ChargesDetail | null
  tds_paise?: Paise
  /** TRANSFER from or to outside: the value on that day, which is the money in or out. */
  value_paise?: Paise | null
  /** SPLIT: quantity x ratio_num / ratio_den ("each becomes 5" is 5 and 1; "1 for 10" is 1 and 10). */
  ratio_num?: number | null
  ratio_den?: number | null
  /** TRANSFER from outside: the date the lot was first bought (default trade_date). */
  lot_date?: Day | null
  note?: string | null
}

export interface Activity {
  id: number
  tab_id: number
  seq: number
  kind: ActivityKind
  status: ActivityStatus
  source: ActivitySource
  trade_date: Day
  account_id: number | null
  to_account_id: number | null
  asset_id: number | null
  qty_u: QtyU | null
  price_u: PriceU | null
  amount_paise: Paise | null
  charges_paise: Paise
  charges_detail: ChargesDetail | null
  tds_paise: Paise
  value_paise: Paise | null
  ratio_num: number | null
  ratio_den: number | null
  lot_date: Day | null
  note: string | null
  batch_id: number | null
  created_at: EpochMs
  updated_at: EpochMs
}

/** What an entry does, in words, before it is saved (a dry run) and after. */
export interface EntryEffect {
  /** "Cost ₹14,302.50 with ₹20.00 charges", or the lots a sale uses and its gain. */
  words: string
  cost_paise: Paise | null
  gain_paise: Paise | null
}

// ---- prices, freshness and the live math ----

export interface PriceInfo {
  price_u: PriceU | null
  /** The close before today (a fund: the NAV before the newest). */
  prev_close_u: PriceU | null
  /** The session, NAV date or point date the price belongs to. */
  day: Day | null
  /** When it was taken: the tick, the record, the entry. */
  at: EpochMs | null
  source: PriceSource | null
  basis: PriceBasis | null
  /** A closing record taken after its time (the service was down at 15:55; 16:00 from 7 Dec 2026). */
  late: boolean
}

export interface Freshness {
  state: FreshnessState
  /** The sentence for a NAV, a manual value or a record; null for a listed price (the feed line speaks). */
  words: string | null
}

/**
 * One account's quantity of one asset, with everything the screen needs to price it as ticks
 * arrive. All integers.
 *
 * today (listed): value now - (open_qty_u at prev_close_u + in_today_paise - out_today_paise),
 * each part rounded to paise (README, "Today").
 */
export interface HoldingLine {
  account_id: number
  qty_u: QtyU
  /** The quantity at the open: after today's splits and bonuses, before today's trades. */
  open_qty_u: QtyU
  /** Cost of the lots held now (qty_u), charges included. */
  cost_paise: Paise
  /**
   * Money that came in today: acquisitions at their gross amounts, shares brought in from outside
   * at the value entered, and shares moved in from another account at the previous close.
   */
  in_today_paise: Paise
  /** Money that went out today, counted the same ways: disposals, shares taken out, shares moved. */
  out_today_paise: Paise
  /** Units in lots held more than a year. */
  over_year_qty_u: QtyU
}

export interface HoldingRow {
  asset: AssetRef
  price: PriceInfo
  fresh: Freshness
  today_mode: TodayMode
  /**
   * A stock that closes by auction: its price waits for the auction from `auction_from`, may come
   * until `auction_until` and is expected at `auction_close` ("price at about 15:30", 15:40 from
   * 7 Dec 2026). All three
   * are null for a stock that does not close by auction; the phone never computes sessions.
   */
  cas: boolean
  auction_from: EpochMs | null
  auction_until: EpochMs | null
  auction_close: EpochMs | null
  lines: HoldingLine[]
}

/** Today for a tab or a row: a figure when there is one, else why not. */
export interface TodayFigure {
  state: TodayState
  paise: Paise | null
  /** What the percentage is of: the open value plus today's money in. */
  base_paise: Paise
  pct: Pct | null
  /** Rows in the figure. */
  counted: number
  /** Rows left out because a price or previous close they need is not known yet. */
  unknown: number
  /** Funds left out because their NAV for today is not published yet. */
  pending: number
}

/** The sums that do not move with a price. */
export interface TabFixed {
  realised_paise: Paise
  income_paise: Paise
  fees_paise: Paise
  money_in_paise: Paise
  money_out_paise: Paise
  /**
   * Today from holdings sold out today (hidden from the list); null when one of them has no
   * previous close, and the tab says a holding sold today is not in the figure.
   */
  closed_today_paise: Paise | null
  closed_today_base_paise: Paise
  /** Outstanding liabilities (a tab with a loan shows net worth). */
  liabilities_paise: Paise
}

/** The service's figures at its own prices; the screen recomputes them as ticks arrive. */
export interface TabTotals {
  value_paise: Paise
  cost_paise: Paise
  unrealised_paise: Paise
  overall_gain_paise: Paise
  absolute_return_pct: Pct | null
  today: TodayFigure
  /** Holdings with no price yet, counted at their cost; the screen says how many. */
  unpriced: number
}

export interface TabLive {
  rows: HoldingRow[]
  fixed: TabFixed
  totals: TabTotals
}

export interface FreshnessNote {
  asset_id: number
  name: string
  kind: 'nav' | 'manual'
  words: string
  price_day: Day | null
}

/**
 * `owing`: a balance to subtract. `zero`: entered at 0, owing nothing. `later`: its balance starts
 * on `day`, after the day shown. For the last two `words` says what that means.
 */
export interface Liability {
  asset_id: number
  name: string
  outstanding_paise: Paise
  day: Day
  state: 'owing' | 'zero' | 'later'
  words: string | null
  fresh: Freshness
}

export interface AllocationRow {
  /** The class, sector, cap, account id, member id or tag it stands for. */
  key: string
  label: string
  value_paise: Paise
  pct: Pct
  holdings: number
}

// ---- filters, layout and saved views ----

export interface Filters {
  accounts: number[]
  classes: AssetClass[]
  tags: string[]
  /** Only lots held over (or under) a year. */
  held: HeldFilter | null
  /** Quantities and cost as at that day, value at that day's prices. */
  as_of: Day | null
}

export interface LayoutSpec {
  v: number
  widgets: Widget[]
  columns: HoldingColumn[]
  sort: Sort
  group: Group
}

export interface LayoutState {
  tab_id: number
  version: number
  spec: LayoutSpec
  /** What "Reset to the defaults of this kind of tab" gives. */
  defaults: LayoutSpec
}

export interface ViewSpec {
  filters: Filters
  sort: Sort
  group: Group
  columns: HoldingColumn[]
}

export interface SavedView {
  id: number
  tab_id: number
  name: string
  spec: ViewSpec
  is_default: boolean
  version: number
}

// ---- a tab ----

export interface TabView extends TabLive {
  tab: TabInfo
  /** The IST day the Today figures are for. */
  today: Day
  as_of: Day | null
  read_at: EpochMs
  filters: Filters
  /** Every asset the tab's entries name, sold out ones too. */
  assets: Asset[]
  liabilities: Liability[]
  /** True when a filter on a part of the tab hides loans that belong to the whole of it. */
  loans_hidden: boolean
  /** Loans on no tab (entered in v0.5.0), each of which can be moved to this one. */
  loose_loans: Liability[]
  allocation: Record<AllocationBy, AllocationRow[]>
  notes: FreshnessNote[]
  layout: LayoutState
  views: SavedView[]
}

// ---- performance ----

export interface XirrFigure {
  state: XirrState
  pct: Pct | null
  /** `later`: the day the XIRR appears (a year after the first flow). */
  from_day: Day | null
  /** The sentence when it is not shown. */
  words: string | null
}

export interface TwrFigure {
  /** The first complete day. */
  start_day: Day | null
  pct: Pct | null
  /** Only beyond 365 days. */
  annualised_pct: Pct | null
  words: string | null
}

export interface BenchmarkFigure {
  benchmark: Benchmark
  label: string
  /** The same money on the same days, put into the index. */
  value_paise: Paise | null
  xirr_pct: Pct | null
  /** The index from the TWR start to now. */
  twr_pct: Pct | null
  /** The index could not have paid every withdrawal, or its prices start too late. */
  flagged: boolean
  /** "Dividends not included", and the flagged sentence. */
  words: string[]
}

export interface SeriesPoint {
  day: Day
  value_paise: Paise
  net_put_in_paise: Paise
}

export interface FyFigure {
  /** The year the financial year starts in: 2026 is FY 2026-27. */
  fy: number
  label: string
  realised_paise: Paise
  income_paise: Paise
  fees_paise: Paise
}

export interface MonthIncome {
  /** YYYYMM. */
  month: number
  paise: Paise
}

/** One financial year's income (M7.29). */
export interface IncomeYear {
  /** The year the financial year starts in: 2026 is FY 2026-27. */
  fy: number
  label: string
  paise: Paise
}

/** Income this financial year, the last twelve months and by year, and the yield on cost. */
export interface IncomeReport {
  this_fy_paise: Paise
  by_month: MonthIncome[]
  /** The twelve months by_month lists, added up. */
  last_12m_paise: Paise
  /** The part of that income the lots still held earned, and what the lots that earned it cost. */
  held_income_paise: Paise
  held_cost_paise: Paise
  /** `held_income_paise` on `held_cost_paise`; null when no lot held earned anything in the twelve months (never 0 in
   *  its place). Income that went with units since sold, and interest on no asset, are not in it. */
  yield_on_cost_pct: Pct | null
  /** Oldest first; only years with income. */
  by_fy: IncomeYear[]
}

export interface PerformanceReport {
  valuation_day: Day
  first_flow_day: Day | null
  value_paise: Paise
  money_in_paise: Paise
  money_out_paise: Paise
  fee_paise: Paise
  /** In + fees - out, up to the valuation day. */
  net_put_in_paise: Paise
  /** Value - net put in. */
  total_gain_paise: Paise
  absolute_return_pct: Pct | null
  xirr: XirrFigure
  twr: TwrFigure
  benchmarks: BenchmarkFigure[]
  series: SeriesPoint[]
  by_fy: FyFigure[]
  income: IncomeReport
  /** "Daily history from 3 Nov 2026, the first day every holding here had a closing price." */
  series_words: string | null
}

// ---- import and export ----

export interface ImportRow {
  /** The row in the file, counting the header as row 1. */
  row: number
  status: ImportRowStatus
  words: string | null
  entry: ActivityInput | null
}

export interface ImportPreview {
  batch_id: number
  format: ImportFormat
  filename: string
  rows_total: number
  rows_new: number
  rows_dup: number
  rows_bad: number
  /** "42 new, 3 already in this tab, 2 with problems". */
  words: string
  /** Problem rows first, then the rest, at most IMPORT_PREVIEW_ROWS. */
  rows: ImportRow[]
  truncated: boolean
  tab_version: number
  expires_at: EpochMs
}

export interface ImportCommitted {
  batch_id: number
  added: number
  tab_version: number
}

export interface ImportDiscarded {
  batch_id: number
  status: 'discarded' | 'undone'
  removed: number
  tab_version: number | null
}

export interface ExportFile {
  /** ASCII: SUDARSHAN_Portfolio_<tab-id>_<YYYY-MM-DD>.csv */
  filename: string
  text: string
}

/** A tab's export (M7.40): the entries in the import format, or one view (phase P7, lane X). */
export interface ExportArgs extends Targets {
  view?: ExportView
}

/** A PDF export, as base64 (the answer is JSON). */
export interface ExportPdf {
  /** ASCII: SUDARSHAN_Portfolio_<tab-slug>_<view>_<YYYY-MM-DD>.pdf */
  filename: string
  base64: string
}

// ---- mutual funds ----

export interface MfScheme {
  amfi_code: string
  name: string
  category: string | null
  amc: string | null
  asset_class: AssetClass
  nav_u: PriceU | null
  nav_day: Day | null
}

export interface MfSearch {
  items: MfScheme[]
  /** Why there is nothing to search ("AMFI's list is not loaded"), when that is the case. */
  note: string | null
}

// ---- the Upstox tab ----

export interface UpstoxHolding {
  instrument_key: string
  /** In OpenAlgo's words, to subscribe with; null when the master does not know it. */
  exchange: string | null
  symbol: string | null
  name: string
  isin: string | null
  qty_u: QtyU
  t1_qty_u: QtyU
  avg_price_u: PriceU
  /** The price at `read_at`: the anchor the screen moves with ticks. */
  last_price_u: PriceU
  close_price_u: PriceU | null
  /** Upstox's own P&L at `last_price_u`. */
  pnl_paise: Paise
  product: string
  collateral_qty_u: QtyU
  collateral_type: string | null
  haircut_pct: Pct | null
  cnc_used_qty_u: QtyU
  cas: boolean
  auction_from: EpochMs | null
  auction_until: EpochMs | null
  auction_close: EpochMs | null
}

export interface UpstoxPosition {
  instrument_key: string
  exchange: string | null
  symbol: string | null
  name: string
  product: string
  /** Net, signed. */
  qty_u: QtyU
  overnight_qty_u: QtyU
  day_buy_qty_u: QtyU
  day_sell_qty_u: QtyU
  avg_price_u: PriceU
  buy_price_u: PriceU
  sell_price_u: PriceU
  last_price_u: PriceU
  close_price_u: PriceU | null
  multiplier: number
  pnl_paise: Paise
  realised_paise: Paise
  unrealised_paise: Paise
  value_paise: Paise
  cas: boolean
  auction_from: EpochMs | null
  auction_until: EpochMs | null
  auction_close: EpochMs | null
}

export interface FundsLine {
  key: string
  label: string
  paise: Paise
}

export interface FundsGroup {
  key: string
  label: string
  total_paise: Paise | null
  lines: FundsLine[]
}

export interface UpstoxFunds {
  available_paise: Paise
  groups: FundsGroup[]
}

/** One part of the Upstox book: the figures, when they were read, and the sentence if they are not fresh. */
export interface UpstoxPart<T> {
  state: PartState
  read_at: EpochMs | null
  /** "Upstox did not answer. Figures from 10:41:05." and the like. */
  note: string | null
  data: T | null
}

/** One order of the day (the family and the journal read it; the portfolio page does not show it). */
export interface UpstoxOrder {
  order_id: string
  exchange_order_id: string | null
  parent_order_id: string | null
  instrument_key: string
  exchange: string | null
  symbol: string | null
  product: string
  order_type: string | null
  validity: string | null
  variety: string | null
  transaction_type: 'BUY' | 'SELL'
  status: string
  status_message: string | null
  is_amo: boolean
  tag: string | null
  qty_u: QtyU
  filled_qty_u: QtyU | null
  pending_qty_u: QtyU | null
  disclosed_qty_u: QtyU | null
  price_u: PriceU | null
  trigger_price_u: PriceU | null
  avg_price_u: PriceU | null
  placed_at: EpochMs | null
  exchange_at: EpochMs | null
}

/** One execution of the day. */
export interface UpstoxTrade {
  trade_id: string
  order_id: string
  exchange_order_id: string | null
  instrument_key: string
  exchange: string | null
  symbol: string | null
  product: string
  order_type: string | null
  transaction_type: 'BUY' | 'SELL'
  qty_u: QtyU
  price_u: PriceU
  amount_paise: Paise
  placed_at: EpochMs | null
  exchange_at: EpochMs | null
}

export interface UpstoxBook {
  holdings?: UpstoxPart<UpstoxHolding[]>
  positions?: UpstoxPart<UpstoxPosition[]>
  mtf?: UpstoxPart<UpstoxPosition[]>
  funds?: UpstoxPart<UpstoxFunds>
  orders?: UpstoxPart<UpstoxOrder[]>
  trades?: UpstoxPart<UpstoxTrade[]>
}

export interface UpstoxEodDay {
  day: Day
  read_at: EpochMs
  /** null: that part was not read at the close (never 0). */
  holdings_value_paise: Paise | null
  holdings_cost_paise: Paise | null
  positions_pnl_paise: Paise | null
  /** One plain sentence for each part that was not read ("Holdings were not read at the close."). */
  not_read?: string[]
  /** Taken at its time and not yet settled to the official close. */
  provisional: boolean
  late: boolean
  /**
   * When that day's record was due: the day's last close plus 15 minutes (15:55; 16:00 from
   * 7 Dec 2026, when F&O closes at 15:45). Null from a server that does not say.
   */
  due_at?: EpochMs | null
}

export interface UpstoxHistory {
  days: UpstoxEodDay[]
  note: string | null
}

// ---- state, summary and health ----

export interface JobState {
  name: JobName
  day: Day | null
  status: JobStatus | null
  detail: string | null
  at: EpochMs | null
}

export interface PortfolioState {
  service_version: string
  schema_version: number
  now: EpochMs
  /** The IST day the figures are for. */
  today: Day
  tabs: TabInfo[]
  members: Member[]
  accounts: Account[]
  jobs: JobState[]
  amfi: { on: boolean; nav_day: Day | null }
  /** Phase P7: which of its views the service has switched on (the owner's answer only). Absent
   *  or false: the view stays hidden and its ops answer `not_ready`. */
  switches?: Record<SwitchName, boolean>
}

export interface UpstoxSummary {
  /** `reading`: the service answered with the tabs and Upstox is still being read; the phone asks
   *  `upstox_book` for the figures. */
  state: PartState | 'reading'
  /** When the figures were read; null when they were not (state `reading` or `unavailable`). */
  read_at: EpochMs | null
  note: string | null
  /** The rows to move with ticks. */
  holdings: UpstoxHolding[]
}

/** One row of the dashboard card: a custom tab (`live`) or the Upstox tab (`upstox`). */
export interface SummaryEntry {
  tab: TabInfo
  live: TabLive | null
  upstox: UpstoxSummary | null
}

export interface PortfolioSummary {
  read_at: EpochMs
  today: Day
  entries: SummaryEntry[]
}

/** The service's unsealed GET /health: counts and job states, no owner data. */
export interface PortfolioHealth {
  ok: boolean
  enabled: boolean
  version: string
  schema: { file: number | null; code: number }
  jobs: JobState[]
  counts: { tabs: number; assets: number; activities: number }
}

// ---- the ops: what each one is sent and what it answers ----

/** A write that changes something with a version sends the version it saw. */
export interface Versioned {
  version: number
}

/** A create sends a client-made id (a UUID); a resend after a timeout returns the row already made. */
export interface Creates {
  request_id: string
}

/** The `<id>` of the route. */
export interface Targets {
  id: number
}

export type NoArgs = Record<string, never>

export interface TabCreateArgs extends Creates {
  name: string
  name_alt?: string | null
  kind: TabKind
  icon?: TabIcon
  colour?: TabColour
  pinned?: boolean
}

export interface TabGetArgs extends Targets {
  as_of?: Day
  /** A saved view: its filters apply unless they are given here. */
  view?: number
  accounts?: number[]
  classes?: AssetClass[]
  tags?: string[]
  held?: HeldFilter
}

export interface TabUpdateArgs extends Targets, Versioned {
  name?: string
  name_alt?: string | null
  icon?: TabIcon
  colour?: TabColour
  position?: number
  pinned?: boolean
  archived?: boolean
  manual_old_days?: number
}

export interface TabDeleteArgs extends Targets, Versioned {
  /** The tab's name, typed by the owner. Only an archived tab can be deleted. */
  confirm_name: string
}

export interface ActivitiesListArgs extends Targets {
  /** The `next` of the previous page. */
  before?: string
  /** Default ACTIVITIES_PAGE, at most ACTIVITIES_PAGE_MAX. */
  limit?: number
  asset?: number
}

/** `id` is the tab; `version` the tab's. */
export interface ActivityCreateArgs extends Targets, Versioned, Creates {
  dry_run?: boolean
  entry: ActivityInput
}

/** `id` is the entry; `version` the tab's; `entry` replaces the whole entry. */
export interface ActivityUpdateArgs extends Targets, Versioned {
  dry_run?: boolean
  entry: ActivityInput
}

/** `id` is the entry; `version` the tab's. */
export type ActivityDeleteArgs = Targets & Versioned

export interface AssetCreateArgs extends Creates {
  kind: AssetKind
  /** Listed: the service finds the instrument. */
  symbol?: string
  exchange?: string
  /** Mutual fund. */
  amfi_code?: string
  /** Manual and liability. */
  name?: string
  asset_class?: AssetClass
  unit_label?: string
  /** A liability: its outstanding balance on a day; a manual asset: its first value. */
  first_value?: { day: Day; price_u: PriceU }
  /** A liability belongs to one tab, whose version moves (M7.19 V2). */
  tab_id?: number
  version?: number
  sector?: string | null
  mcap?: Mcap | null
  tags?: string[]
}

export interface AssetUpdateArgs extends Targets, Versioned {
  /** A manual asset's own name only. */
  name?: string
  asset_class?: AssetClass
  sector?: string | null
  mcap?: Mcap | null
  tags?: string[]
}

/** `id` is the asset. */
export interface PricePointSetArgs extends Targets {
  day: Day
  /** A loan's balance may be 0 (paid off); a value kept by hand is above zero. */
  price_u: PriceU
  /** A loan's balance is set from the tab it is on, at that tab's version (M7.19 V2). */
  tab_id?: number
  version?: number
}

/** `id` is a loan that is on no tab (one entered in v0.5.0); it joins `tab_id`. */
export interface LoanMoveArgs extends Targets {
  tab_id: number
  version: number
}

export interface MfSearchArgs {
  q: string
  /** At most MF_SEARCH_MAX. */
  limit?: number
}

export interface AccountCreateArgs extends Creates {
  member_id: number
  name: string
  kind: AccountKind
  broker?: string | null
  note?: string | null
}

export interface AccountUpdateArgs extends Targets, Versioned {
  name?: string
  kind?: AccountKind
  broker?: string | null
  note?: string | null
}

export interface MemberCreateArgs extends Creates {
  name: string
  relation?: string | null
}

export interface MemberUpdateArgs extends Targets, Versioned {
  name?: string
  relation?: string | null
}

/** `id` is the tab. `text` is the file as UTF-8 text (at most IMPORT_MAX_FILE_BYTES, 5,000 rows). */
export interface ImportPreviewArgs extends Targets {
  format: ImportFormat
  filename: string
  text: string
  /** The account for rows that do not name one. */
  account_id?: number
}

/** `id` is the batch; `version` the tab version the preview saw. */
export type ImportCommitArgs = Targets & Versioned

/** `id` is the batch; `version`: the tab's, needed to undo a committed import. */
export interface ImportDiscardArgs extends Targets {
  version?: number
}

export interface LayoutSetArgs extends Targets, Versioned {
  /** null: back to the defaults of this kind of tab. */
  spec: LayoutSpec | null
}

export interface ViewCreateArgs extends Targets, Creates {
  name: string
  spec: ViewSpec
  is_default?: boolean
}

export interface ViewUpdateArgs extends Targets, Versioned {
  name?: string
  spec?: ViewSpec
  is_default?: boolean
}

export interface UpstoxBookArgs {
  parts?: UpstoxPartName[]
  /** Seconds: a part read more recently than this is answered from memory. */
  max_age?: number
}

export interface UpstoxHistoryArgs {
  /** Days, newest last; default and most UPSTOX_HISTORY_DAYS. */
  limit?: number
}

/** One of the last REPORT_YEARS financial years (the year it starts in) and a segment. */
export interface ReportArgs {
  fy: number
  segment: ReportSegment
  /** Read it from Upstox now, whatever the cache holds (the budget still limits it). */
  force?: boolean
}

// ---- Upstox's reports (M7.6 trade history, M7.7 trade-wise P&L and charges) ----

export interface ReportYear {
  /** The year the financial year starts in: 2026 is FY 2026-27. */
  fy: number
  label: string
  current: boolean
}

export interface ReportYears {
  today: Day
  years: ReportYear[]
}

/** What every report answer carries: not market data, so the time it was read is shown. */
export interface ReportPart {
  state: ReportState
  /** When Upstox last answered in full; null until it has. */
  read_at: EpochMs | null
  /** A sentence when the figures are not complete or not fresh. */
  note: string | null
  fy: number
  segment: ReportSegment
}

/** One executed trade as Upstox's trade history lists it (`/v2/charges/historical-trades`). */
export interface TradeRow {
  trade_id: string
  trade_date: Day
  exchange: string
  segment: ReportSegment
  transaction_type: 'BUY' | 'SELL'
  scrip_name: string
  symbol: string | null
  isin: string | null
  instrument_token: string | null
  option_type: 'CE' | 'PE' | null
  strike_price_u: PriceU | null
  expiry: Day | null
  qty_u: QtyU
  price_u: PriceU
  amount_paise: Paise
}

export interface TradesReport extends ReportPart {
  rows: TradeRow[]
  /** Upstox's count for the year and segment, when its answer carried one. */
  total_records: number | null
  bought_paise: Paise
  sold_paise: Paise
}

/** One matched buy and sell as Upstox's trade-wise P&L report lists it. */
export interface PnlRow {
  scrip_name: string
  isin: string | null
  trade_type: 'EQ' | 'FUT' | 'OPT' | string
  qty_u: QtyU
  buy_date: Day | null
  sell_date: Day | null
  buy_average_u: PriceU
  sell_average_u: PriceU
  buy_amount_paise: Paise
  sell_amount_paise: Paise
  /** sell amount - buy amount, before charges. */
  pnl_paise: Paise
}

/** Upstox's charges on the year's trades (`/v2/trade/profit-loss/charges`); null where it gave none. */
export interface ChargesBreakdown {
  total_paise: Paise | null
  brokerage_paise: Paise | null
  gst_paise: Paise | null
  stt_paise: Paise | null
  stamp_duty_paise: Paise | null
  transaction_paise: Paise | null
  clearing_paise: Paise | null
  ipft_paise: Paise | null
  others_paise: Paise | null
  sebi_turnover_paise: Paise | null
  demat_transaction_paise: Paise | null
}

export interface PnlReport extends ReportPart {
  rows: PnlRow[]
  /** Upstox's count of trades in the report; null until the metadata was read. */
  trades_count: number | null
  realised_paise: Paise
  charges: ChargesBreakdown | null
  /** realised less the charges total; null until the charges were read. */
  net_paise: Paise | null
}

export interface ActivitiesPage {
  items: Activity[]
  /** Pass as `before` for the next page; null on the last. */
  next: string | null
}

export interface ActivitySaved {
  /** null for a dry run. */
  activity: Activity | null
  effect: EntryEffect
  tab_version: number
}

export interface ActivityDeleted {
  id: number
  tab_version: number
}

export interface Deleted {
  id: number
}

export interface ViewList {
  views: SavedView[]
}

// ---- what the owner shares with the family (M7.37; cross-design Y6) ----

/**
 * A tab shared with one family member (`viewer` is the member's id, "12") or with every member of
 * a role ("role:child"). An ended share stays until the owner removes it, and while a member's own
 * share stays, ended or not, it is the one that counts for them, not their role's.
 */
export interface TabShare {
  tab_id: number
  viewer: string
  level: ShareLevel
  expires_at: EpochMs | null
  version: number
}

/** A member's privacy floor: what the portfolio hides from them whatever the family view asks. */
export interface MemberPrivacy {
  member: string
  role: string | null
  shown: Record<PrivacyCategory, boolean>
  version: number
}

export interface SharesListArgs {
  tab_id?: number
  viewer?: string
}

export interface SharesList {
  shares: TabShare[]
  /** Every member who has a floor; a member with none is shown no figures. */
  privacy: MemberPrivacy[]
}

/** A new share is sent with `version: null`; a change names the version it was decided on. */
export interface ShareSetArgs {
  tab_id: number
  viewer: string
  level: ShareLevel
  expires_at: EpochMs | null
  version: number | null
}

export interface ShareRemoveArgs {
  tab_id: number
  viewer: string
  version: number
}

export interface ShareRemoved {
  removed: boolean
}

/** A first floor is sent with `version: null`. P&L shown needs quantities shown (Y24). */
export interface MemberPrivacySetArgs {
  member: string
  role: string | null
  shown: Record<PrivacyCategory, boolean>
  version: number | null
}

// ---- phase P7: the tax working paper (M7.6, M7.7, M7.27, M7.28, M15.5) ----
// Every figure is computed by the tax core (sudarshan/tax) in the service; the phone formats them
// and computes none. Not market data: each answer carries when it was worked out. The pack is the
// core's own (tax/pack.py, design p7 4.9), the one shape the phone and the journal read.

export interface TaxGetArgs {
  /** The year the financial year starts in (2026 is FY 2026-27); none is the year running. */
  fy?: number
  /** The taxpayer, a member's id; none is the owner. */
  member?: number
}

/** One input the paper was worked out from, and how complete it is. */
export interface TaxSource {
  kind: ReportKind | 'ledger'
  fy: number
  segment: ReportSegment | null
  state: ReportState
  read_at: EpochMs | null
}

export type TaxTerm = 'short' | 'long'

/** One sale, or the part of one taken from one purchase, worked out. */
export interface TaxGain {
  taxpayer: number
  account: number
  isin: string
  /** null: no purchase record; such a sale is listed apart and never costed at zero. */
  acquired_day: Day | null
  sale_day: Day
  qty_u: QtyU
  /** What was received. */
  receipt_paise: Paise
  /** The sale value used: nil for a buyback taxed as a dividend. */
  sale_paise: Paise
  cost_paise: Paise | null
  expenses_paise: Paise
  /** Negative is a loss; null when the sale is left out of the totals. */
  gain_paise: Paise | null
  term: TaxTerm | null
  rate_bp: number | null
  status: 'ok' | 'needs_fmv' | 'unknown_cost' | 'exempt_buyback'
  /** The 31 Jan 2018 price set the cost. */
  fmv_used: boolean
  /** The expenses are the year's charges shared over its sales. */
  allocated: boolean
  /** A buyback's regime on its day; null for a sale. */
  buyback: 'exempt' | 'dividend_and_loss' | 'capital_gain' | null
  dividend_paise: Paise
}

/** The year's gains and losses at one term and rate. */
export interface TaxBucket {
  term: TaxTerm
  rate_bp: number
  gain_paise: Paise
  loss_paise: Paise
}

/** One step of a set-off: a loss used against a gain or a profit. */
export interface TaxSetOffStep {
  step: 'same_year' | 'brought_forward'
  bucket: CarryBucket
  loss_fy: number
  against: TaxTerm | 'business' | 'speculative'
  /** Capital gains only: the rate of the gain it reduced. */
  rate_bp?: number
  paise: Paise
}

/** A loss carried to later years, lapsed, or computed beside the owner's own figure. */
export interface TaxCarry {
  loss_fy: number
  bucket: CarryBucket
  paise: Paise
  source: 'owner' | 'computed'
  /** The last financial year it can be set off in. */
  last_fy: number
}

export interface TaxExemption {
  limit_paise: Paise
  used_paise: Paise
  order: ExemptOrder
  by_rate: { rate_bp: number; paise: Paise }[]
}

export interface TaxLine {
  term: TaxTerm
  rate_bp: number
  taxable_paise: Paise
  tax_paise: Paise
}

export interface IndicativeTax {
  lines: TaxLine[]
  tax_paise: Paise
  cess_bp: number
  cess_paise: Paise
  cess_label: string
}

export interface CapitalGains {
  fy: number
  schedule: TaxGain[]
  by_bucket: TaxBucket[]
  st_gain_paise: Paise
  st_loss_paise: Paise
  lt_gain_paise: Paise
  lt_loss_paise: Paise
  set_off: TaxSetOffStep[]
  /** What was set off against what, as sentences. */
  set_off_words: string[]
  exemption: TaxExemption
  taxable: { st_paise: Paise; lt_paise: Paise }
  indicative_tax: IndicativeTax
  carried: TaxCarry[]
  lapsed: TaxCarry[]
  own_figures: TaxCarry[]
  needs_fmv: TaxGain[]
  unknown_cost: TaxGain[]
  exempt_buybacks: TaxGain[]
  expenses_allocated: boolean
}

export interface BusinessKind {
  gross_paise: Paise
  turnover_paise: Paise
  count: number
}

export interface BusinessBlock {
  gross_paise: Paise
  /** Every part of the charges given, STT included. */
  charges_paise: Paise
  net_paise: Paise
  turnover_paise: Paise
  by_kind: Partial<Record<'futures' | 'options' | 'speculative', BusinessKind>>
}

/** A derivative contract: exchange, scrip, expiry, strike (0 for a future), CE, PE or FUT. */
export type TaxContract = [string, string, Day, PriceU, 'CE' | 'PE' | 'FUT']
/** A position still open, or past expiry with no settlement price: taxpayer, account,
 * contract, side, the day it opened, quantity and value. */
export type TaxPosition = [number, number, TaxContract, 'BUY' | 'SELL', Day, QtyU, Paise]

/** A scrip whose profit or loss differs from Upstox's report by more than a rupee. */
export interface TaxDiffers {
  scrip: string
  own_paise: Paise | null
  upstox_paise: Paise | null
}

export interface BusinessIncome {
  non_speculative: BusinessBlock
  speculative: BusinessBlock
  turnover_method: TurnoverMethod
  turnover_method_label: string
  turnover_paise: Paise
  /** Sentences that ask the owner to check with the CA, never a verdict. */
  flags: string[]
  taxable: { non_speculative_paise: Paise; speculative_paise: Paise }
  available_other_heads_paise: Paise
  set_off: TaxSetOffStep[]
  set_off_words: string[]
  carried: TaxCarry[]
  lapsed: TaxCarry[]
  own_figures: TaxCarry[]
  differs: TaxDiffers[]
  unknown_settlement: TaxPosition[]
  open: TaxPosition[]
  assumed_zero_count: number
}

export interface TaxPayer {
  isin: string
  name: string | null
  expected_paise: Paise
  confirmed_paise: Paise
  tds_likely: boolean
}

export interface TaxDividends {
  /** From release 2 (M7.29); empty before. */
  by_payer: TaxPayer[]
  buyback_paise: Paise
  total_paise: Paise
  tds_expected_paise: Paise | null
  unconfirmed_count: number
}

export interface TaxCharges {
  /** Upstox's own breakdown per segment for the year; a segment not read is absent. */
  by_segment: Partial<Record<ReportSegment, ChargesBreakdown>>
  state: ReportState
}

/** A sale the paper does not compute (a manual asset, a fund that is not equity). */
export interface TaxSaleOutside {
  instrument: string
  day: Day
  amount_paise: Paise
}

export interface TaxInputsUsed {
  sources: TaxSource[]
  /** What the paper lacks, as lines the owner can act on. */
  missing: string[]
}

/** The working paper of one financial year and one taxpayer (design p7 4.9). */
export interface TaxPack {
  fy: number
  fy_label: string
  taxpayer: number
  as_of_ms: EpochMs
  as_of_day: Day
  /** The year running: every figure is to date. */
  running: boolean
  /** The working-paper sentence. */
  note: string
  rules_version: string
  inputs: TaxInputsUsed
  capital_gains: CapitalGains
  business: BusinessIncome
  dividends: TaxDividends
  charges: TaxCharges
  not_computed: string[]
  not_computed_sales: TaxSaleOutside[]
  notes: string[]
}

export interface TaxInputsArgs {
  member?: number
}

export interface TaxOpeningLot {
  id: number
  member_id: number
  account_id: number
  isin: string
  acquired_day: Day
  qty_u: QtyU
  cost_paise: Paise
  source: 'owner' | 'upstox_pair'
  note: string | null
  version: number
}

/** The highest price of a share on 31 Jan 2018. */
export interface TaxFmv {
  id: number
  isin: string
  fmv_u: PriceU
  source: 'owner' | 'file'
  version: number
}

export interface TaxCarryInput {
  id: number
  member_id: number
  loss_fy: number
  bucket: CarryBucket
  paise: Paise
  source: 'owner' | 'computed'
  note: string | null
  version: number
}

/** A day's trading in one share that the owner marked as delivery, not intraday. */
export interface TaxDayOverride {
  id: number
  member_id: number
  account_id: number
  isin: string
  day: Day
  treat: 'delivery'
  version: number
}

/** A sale the owner marked as a buyback tender, or one the history does not list. */
export interface TaxTradeMark {
  id: number
  member_id: number
  account_id: number
  isin: string
  day: Day
  qty_u: QtyU
  price_u: PriceU
  mark: 'buyback'
  trade_id: string | null
  version: number
}

/** A taxpayer's readings of the open rules; null is the rule's own default reading. */
export interface TaxSettings {
  member_id: number
  turnover_method: TurnoverMethod | null
  exempt_order: ExemptOrder | null
  lt_boundary: LtBoundary | null
  allocate_expenses: boolean
  /** 0 until first saved. */
  version: number
}

/** An account of the taxpayer and how it counts: by Upstox's reports, by its ledger, or not. */
export interface TaxAccount {
  id: number
  name: string
  kind: AccountKind
  tax_mode: TaxMode
}

export interface TaxInputs {
  member_id: number
  accounts: TaxAccount[]
  opening_lots: TaxOpeningLot[]
  fmvs: TaxFmv[]
  carries: TaxCarryInput[]
  day_overrides: TaxDayOverride[]
  trade_marks: TaxTradeMark[]
  settings: TaxSettings
}

export type TaxInputRecord =
  | TaxOpeningLot
  | TaxFmv
  | TaxCarryInput
  | TaxDayOverride
  | TaxTradeMark
  | TaxSettings

export interface TaxInputSetArgs {
  kind: TaxInputKind
  /** The row's own fields, as the matching record holds them (no id, source or version). */
  row: Record<string, unknown>
  /** An existing row and the version it was read at; none for a new one. The readings always
   * send the version they were read at (0 before the first save). */
  id?: number
  version?: number
  request_id?: string
  /** Change nothing; answer what the change would do to the paper of ``fy``. */
  dry_run?: boolean
  /** The year whose figures a dry run compares; none is the year running. */
  fy?: number
}

export interface TaxInputSaved {
  kind: TaxInputKind
  /** null for a dry run. */
  row: TaxInputRecord | null
  /** "This changes long-term gain from Rs 30,000 to Rs 15,000." */
  effect: string[]
}

export interface TaxInputDeleteArgs extends Targets, Versioned {
  kind: TaxInputKind
}

export interface TaxCsvArgs {
  fy?: number
  member?: number
  schedule: TaxSchedule
}

export interface ReportCsvArgs {
  kind: ReportKind
  fy: number
  segment: ReportSegment
}

// ---- phase P7: corporate actions, dividends, the calendar (M7.13, M7.29, M7.36) ----

export interface CaListArgs {
  /** `upstox` or `tab:<id>`; none is every scope. */
  scope?: string
  from?: Day
  to?: Day
}

export interface CaDecisionRow {
  id: number
  event_id: number
  scope: string
  state: CaDecision
  ratio_num: number
  ratio_den: number
  /** The entry a tab got from it, when the owner added one. */
  activity_id: number | null
  version: number
}

export interface CaDetail {
  name: string
  value: string
}

/** One event as Upstox lists it: public facts, no quantity. */
export interface CaEvent {
  id: number
  isin: string
  name: string | null
  kind: CaKind
  ex_day: Day
  /** Per share (a dividend); null when none is given. */
  amount_u: PriceU | null
  ratio_num: number | null
  ratio_den: number | null
  details: CaDetail[]
  read_at: EpochMs
  /** Upstox no longer lists it; it is kept, never deleted. */
  gone_at: EpochMs | null
  decisions: CaDecisionRow[]
}

export interface CaRead {
  isin: string
  read_at: EpochMs | null
  state: string
  note: string | null
}

export interface CaList {
  events: CaEvent[]
  /** Bonuses and splits waiting for the owner's one tap. */
  to_confirm: CaEvent[]
  reads: CaRead[]
}

export interface CaDecideArgs extends Targets {
  scope: string
  state: CaDecision
  ratio_num: number
  ratio_den: number
  version?: number
  request_id?: string
}

export interface DividendsArgs {
  fy?: number
  member?: number
}

export interface DividendLine {
  id: number
  member_id: number
  account_id: number
  isin: string
  name: string | null
  event_id: number | null
  fy: number
  ex_day: Day
  qty_u: QtyU
  expected_paise: Paise
  state: DividendState
  credited_paise: Paise | null
  tds_paise: Paise | null
  credited_day: Day | null
  version: number
}

export interface DividendPayer {
  isin: string
  name: string | null
  lines: DividendLine[]
  expected_paise: Paise
  confirmed_paise: Paise
  tds_likely: boolean
  tds_expected_paise: Paise | null
}

export interface DividendsYear {
  payers: DividendPayer[]
  total_paise: Paise
  tds_expected_paise: Paise
  unconfirmed_count: number
}

export interface DividendsReport {
  fy: number
  member_id: number
  as_of: EpochMs
  dividends: DividendsYear
}

export interface DividendSetArgs extends Targets, Versioned {
  state: DividendState
  credited_paise?: Paise | null
  tds_paise?: Paise | null
  credited_day?: Day | null
}

export interface CalendarArgs {
  from?: Day
  to?: Day
}

export interface CalendarRow {
  event_id: number
  isin: string
  name: string | null
  kind: CaKind
  ex_day: Day
  amount_u: PriceU | null
  ratio_num: number | null
  ratio_den: number | null
  /** What is held the day before the ex-date; null for an instrument not held. */
  held_qty_u: QtyU | null
  expected_paise: Paise | null
  checked_at: EpochMs | null
}

export interface CalendarReport {
  from: Day
  to: Day
  /** Upstox lists events to come; false: only those already announced. */
  listed_future: boolean
  calendar: CalendarRow[]
}

// ---- phase P7: risk, settings and goals (M7.32, M7.34) ----

export interface RiskArgs {
  /** A custom tab; none is every tab together. */
  tab?: number
}

/** A figure, or null with the sentence that says how many more days it needs. */
export interface RiskStat {
  value: Pct | null
  note: string | null
}

export interface RiskDrawdown {
  pct: Pct | null
  peak_day: Day | null
  trough_day: Day | null
  recovered_day: Day | null
  note: string | null
}

export interface RiskWeight {
  label: string
  weight_pct: Pct
  over_limit: boolean
}

export interface RiskConcentration {
  holdings: RiskWeight[]
  sectors: RiskWeight[]
  untagged_pct: Pct | null
  flags: string[]
}

export interface RiskOverlap {
  tab_id: number
  weight_pct: Pct
  names: string[]
}

export interface RiskEmergency {
  months: Pct | null
  target_months: number
  note: string | null
}

export interface RiskFigures {
  days: number
  drawdown: RiskDrawdown
  volatility: RiskStat
  sharpe: RiskStat
  sortino: RiskStat
  calmar: RiskStat
  concentration: RiskConcentration
  overlap: RiskOverlap[]
  emergency: RiskEmergency
}

export interface RiskReport {
  tab_id: number | null
  as_of_day: Day
  /** "On closing values up to 13 Nov 2026." */
  basis: string
  risk: RiskFigures
}

export interface OwnerSettings {
  risk_free_bp: number
  max_holding_pct: Pct
  max_sector_pct: Pct
  monthly_expenses_paise: Paise | null
  months_target: number
  version: number
}

export interface SettingsSetArgs extends Versioned {
  values: Partial<Omit<OwnerSettings, 'version'>>
}

export interface Goal {
  id: number
  member_id: number
  name: string
  name_alt: string | null
  target_paise: Paise
  target_day: Day
  /** The owner's assumed return, basis points a year. */
  rate_bp: number
  tab_ids: number[]
  fixed_paise: Paise | null
  archived: boolean
  version: number
  value_now_paise: Paise
  progress_pct: Pct
  required_monthly_paise: Paise
  months_left: number
  on_track: boolean
}

export interface GoalsList {
  as_of: EpochMs
  goals: Goal[]
}

export interface GoalCreateArgs extends Creates {
  name: string
  name_alt?: string | null
  member_id?: number
  target_paise: Paise
  target_day: Day
  rate_bp?: number
  tab_ids?: number[]
  fixed_paise?: Paise | null
}

export interface GoalUpdateArgs extends Targets, Versioned {
  name?: string
  name_alt?: string | null
  target_paise?: Paise
  target_day?: Day
  rate_bp?: number
  tab_ids?: number[]
  fixed_paise?: Paise | null
  archived?: boolean
}

/** The op name to what it is sent and what its `data` is. The test holds this to OPS. */
export interface PortfolioOps {
  state: { args: NoArgs; data: PortfolioState }
  summary: { args: NoArgs; data: PortfolioSummary }
  tab_create: { args: TabCreateArgs; data: TabInfo }
  tab_get: { args: TabGetArgs; data: TabView }
  tab_update: { args: TabUpdateArgs; data: TabInfo }
  tab_delete: { args: TabDeleteArgs; data: Deleted }
  activities_list: { args: ActivitiesListArgs; data: ActivitiesPage }
  activity_create: { args: ActivityCreateArgs; data: ActivitySaved }
  activity_update: { args: ActivityUpdateArgs; data: ActivitySaved }
  activity_delete: { args: ActivityDeleteArgs; data: ActivityDeleted }
  asset_create: { args: AssetCreateArgs; data: Asset }
  asset_update: { args: AssetUpdateArgs; data: Asset }
  price_point_set: { args: PricePointSetArgs; data: PricePoint }
  loan_move: { args: LoanMoveArgs; data: Asset }
  mf_search: { args: MfSearchArgs; data: MfSearch }
  account_create: { args: AccountCreateArgs; data: Account }
  account_update: { args: AccountUpdateArgs; data: Account }
  member_create: { args: MemberCreateArgs; data: Member }
  member_update: { args: MemberUpdateArgs; data: Member }
  import_preview: { args: ImportPreviewArgs; data: ImportPreview }
  import_commit: { args: ImportCommitArgs; data: ImportCommitted }
  import_discard: { args: ImportDiscardArgs; data: ImportDiscarded }
  export_csv: { args: ExportArgs; data: ExportFile }
  performance: { args: Targets; data: PerformanceReport }
  layout_get: { args: Targets; data: LayoutState }
  layout_set: { args: LayoutSetArgs; data: LayoutState }
  view_list: { args: Targets; data: ViewList }
  view_create: { args: ViewCreateArgs; data: SavedView }
  view_update: { args: ViewUpdateArgs; data: SavedView }
  view_delete: { args: Targets & Versioned; data: Deleted }
  upstox_book: { args: UpstoxBookArgs; data: UpstoxBook }
  upstox_history: { args: UpstoxHistoryArgs; data: UpstoxHistory }
  report_years: { args: NoArgs; data: ReportYears }
  report_trades: { args: ReportArgs; data: TradesReport }
  report_pnl: { args: ReportArgs; data: PnlReport }
  shares_list: { args: SharesListArgs; data: SharesList }
  share_set: { args: ShareSetArgs; data: TabShare }
  share_remove: { args: ShareRemoveArgs; data: ShareRemoved }
  member_privacy_set: { args: MemberPrivacySetArgs; data: MemberPrivacy }
  tax_get: { args: TaxGetArgs; data: TaxPack }
  tax_inputs: { args: TaxInputsArgs; data: TaxInputs }
  tax_input_set: { args: TaxInputSetArgs; data: TaxInputSaved }
  tax_input_delete: { args: TaxInputDeleteArgs; data: Deleted }
  tax_csv: { args: TaxCsvArgs; data: ExportFile }
  report_csv: { args: ReportCsvArgs; data: ExportFile }
  ca_list: { args: CaListArgs; data: CaList }
  ca_decide: { args: CaDecideArgs; data: CaDecisionRow }
  dividends: { args: DividendsArgs; data: DividendsReport }
  dividend_set: { args: DividendSetArgs; data: DividendLine }
  calendar: { args: CalendarArgs; data: CalendarReport }
  risk: { args: RiskArgs; data: RiskReport }
  settings_get: { args: NoArgs; data: OwnerSettings }
  settings_set: { args: SettingsSetArgs; data: OwnerSettings }
  goals_list: { args: NoArgs; data: GoalsList }
  goal_create: { args: GoalCreateArgs; data: GoalsList }
  goal_update: { args: GoalUpdateArgs; data: GoalsList }
  export_pdf: { args: ExportArgs; data: ExportPdf }
}

export type OpName = keyof PortfolioOps
export type OpClass = 'read' | 'write' | 'import' | 'upstox'
export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'

/** One route of the bridge. `query` names the query-string arguments of a GET. */
export interface OpSpec {
  name: OpName
  method: HttpMethod
  route: string
  cls: OpClass
  query?: readonly (readonly [string, QueryType])[]
  upload?: boolean
  download?: boolean
}
