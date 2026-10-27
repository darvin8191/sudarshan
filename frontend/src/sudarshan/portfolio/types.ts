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
export const NAV_OLD_BUSINESS_DAYS = 3
export const XIRR_MIN_DAYS = 365
export const IMPORT_MAX_FILE_BYTES = 1_200_000
export const IMPORT_MAX_ROWS = 5_000
export const LAYOUT_VERSION = 1

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
export const UPSTOX_PARTS = ['holdings', 'positions', 'mtf', 'funds'] as const
export type UpstoxPartName = (typeof UPSTOX_PARTS)[number]
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
export const QUERY_TYPES = ['int', 'day', 'str', 'ints', 'strs'] as const
export type QueryType = (typeof QUERY_TYPES)[number]

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
    'This file is larger than 1.2 MB. Download it in shorter date ranges and import each part.',
  whole_shares: 'Shares are whole numbers.',
  forbidden: 'Only the account owner can use the portfolio.',
  unauthorized: 'Your session has ended. Log in again.',
  rate_limited: 'Too many requests. Wait a minute, then try again.',
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
  /** A closing record taken after its time (the service was down at 15:55). */
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
  /** Cost of the open lots, charges included. */
  cost_paise: Paise
  /** Today's acquisitions at their gross amounts; a transfer between accounts at the previous close. */
  in_today_paise: Paise
  /** Today's disposals at their gross amounts. */
  out_today_paise: Paise
  /** Units in lots held more than a year. */
  over_year_qty_u: QtyU
}

export interface HoldingRow {
  asset: AssetRef
  price: PriceInfo
  fresh: Freshness
  today_mode: TodayMode
  /** A stock that closes by auction: its price waits for the auction from `auction_from`. */
  cas: boolean
  auction_from: EpochMs | null
  auction_until: EpochMs | null
  lines: HoldingLine[]
}

/** Today for a tab or a row: a figure when there is one, else why not. */
export interface TodayFigure {
  state: TodayState
  paise: Paise | null
  /** What the percentage is of: the open value plus today's money in. */
  base_paise: Paise
  pct: Pct | null
  /** Rows in the figure; rows left out because their previous close or NAV is not known yet. */
  counted: number
  unknown: number
  pending: number
}

/** The sums that do not move with a price. */
export interface TabFixed {
  realised_paise: Paise
  income_paise: Paise
  fees_paise: Paise
  money_in_paise: Paise
  money_out_paise: Paise
  /** Today from holdings sold out today (hidden from the list); null when a previous close is unknown. */
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

export interface Liability {
  asset_id: number
  name: string
  outstanding_paise: Paise
  day: Day
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
  income: { this_fy_paise: Paise; by_month: MonthIncome[] }
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

export interface UpstoxBook {
  holdings?: UpstoxPart<UpstoxHolding[]>
  positions?: UpstoxPart<UpstoxPosition[]>
  mtf?: UpstoxPart<UpstoxPosition[]>
  funds?: UpstoxPart<UpstoxFunds>
}

export interface UpstoxEodDay {
  day: Day
  read_at: EpochMs
  holdings_value_paise: Paise
  holdings_cost_paise: Paise
  positions_pnl_paise: Paise
  /** Taken at 15:55 and not yet settled to the official close. */
  provisional: boolean
  late: boolean
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
}

export interface UpstoxSummary {
  state: PartState
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
  first_value?: { day: Day; price_u: PriceU }
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
  price_u: PriceU
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

/** `id` is the tab. `text` is the file as UTF-8 text (at most 1,200,000 bytes, 5,000 rows). */
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
  mf_search: { args: MfSearchArgs; data: MfSearch }
  account_create: { args: AccountCreateArgs; data: Account }
  account_update: { args: AccountUpdateArgs; data: Account }
  member_create: { args: MemberCreateArgs; data: Member }
  member_update: { args: MemberUpdateArgs; data: Member }
  import_preview: { args: ImportPreviewArgs; data: ImportPreview }
  import_commit: { args: ImportCommitArgs; data: ImportCommitted }
  import_discard: { args: ImportDiscardArgs; data: ImportDiscarded }
  export_csv: { args: Targets; data: ExportFile }
  performance: { args: Targets; data: PerformanceReport }
  layout_get: { args: Targets; data: LayoutState }
  layout_set: { args: LayoutSetArgs; data: LayoutState }
  view_list: { args: Targets; data: ViewList }
  view_create: { args: ViewCreateArgs; data: SavedView }
  view_update: { args: ViewUpdateArgs; data: SavedView }
  view_delete: { args: Targets & Versioned; data: Deleted }
  upstox_book: { args: UpstoxBookArgs; data: UpstoxBook }
  upstox_history: { args: UpstoxHistoryArgs; data: UpstoxHistory }
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
