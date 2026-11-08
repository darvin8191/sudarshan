// SUDARSHAN M9.3: what a screen can say, as the phone's builder offers it. A table mirroring
// sudarshan/screener/catalog.py (the service checks every screen against that one, in the
// owner's words; test_ui_catalog_matches.py fails when the two name different things). It holds
// only what the builder needs to offer a choice and to write a clause as a sentence.
import type { Clause, Group, GroupKind, Node, Offset, Operand, TimeFrame } from './types'

export interface FieldInfo {
  id: string
  label: string
  /** True: read from a candle (takes a timeframe and an offset); false: a level of the session. */
  candle: boolean
}

export interface ParamInfo {
  name: string
  kind: 'int' | 'dec' | 'choice'
  default: number | string
  low: number | null
  high: number | null
  choices: string[]
}

export interface IndicatorInfo {
  id: string
  label: string
  /** 'library' ones count toward the 12 instances a screen may hold; 'price' ones do not. */
  family: 'library' | 'price'
  params: ParamInfo[]
  outs: string[]
  /** The timeframes it can be read on; null: any. */
  tfs: TimeFrame[] | null
}

export interface OperatorInfo {
  id: string
  label: string
  arity: 'binary' | 'unary'
  args: string[]
}

export interface OptionInfo {
  id: string
  label: string
  timed: boolean
  underlying: boolean
}

export const FIELDS: FieldInfo[] = [
  { id: 'open', label: 'Open', candle: true },
  { id: 'high', label: 'High', candle: true },
  { id: 'low', label: 'Low', candle: true },
  { id: 'close', label: 'Close', candle: true },
  { id: 'volume', label: 'Volume', candle: true },
  { id: 'oi', label: 'Open interest', candle: true },
  { id: 'vwap', label: 'VWAP', candle: true },
  { id: 'change_pct', label: '% change from the previous close', candle: true },
  { id: 'turnover', label: 'Turnover', candle: true },
  { id: 'prev_close', label: 'Previous close', candle: false },
  { id: 'day_open', label: 'Day open', candle: false },
  { id: 'day_high', label: 'Day high', candle: false },
  { id: 'day_low', label: 'Day low', candle: false },
  { id: 'prev_day_high', label: "Yesterday's high", candle: false },
  { id: 'prev_day_low', label: "Yesterday's low", candle: false },
  { id: 'prev_day_close', label: "Yesterday's close", candle: false },
  { id: 'year_high', label: '52-week high', candle: false },
  { id: 'year_low', label: '52-week low', candle: false },
  { id: 'gap_pct', label: 'Opening gap %', candle: false },
]

export const INDICATORS: IndicatorInfo[] = [
  {
    id: 'sma',
    label: 'SMA',
    family: 'library',
    params: [
      { name: 'length', kind: 'int', default: 20, low: 1, high: 500, choices: [] },
      {
        name: 'source',
        kind: 'choice',
        default: 'close',
        low: null,
        high: null,
        choices: ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'volume', 'oi', 'obv'],
      },
    ],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'wma',
    label: 'WMA',
    family: 'library',
    params: [
      { name: 'length', kind: 'int', default: 20, low: 1, high: 500, choices: [] },
      {
        name: 'source',
        kind: 'choice',
        default: 'close',
        low: null,
        high: null,
        choices: ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'volume', 'oi', 'obv'],
      },
    ],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'vwma',
    label: 'VWMA',
    family: 'library',
    params: [
      { name: 'length', kind: 'int', default: 20, low: 1, high: 500, choices: [] },
      {
        name: 'source',
        kind: 'choice',
        default: 'close',
        low: null,
        high: null,
        choices: ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4'],
      },
    ],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'ema',
    label: 'EMA',
    family: 'library',
    params: [
      { name: 'length', kind: 'int', default: 20, low: 1, high: 500, choices: [] },
      {
        name: 'source',
        kind: 'choice',
        default: 'close',
        low: null,
        high: null,
        choices: ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'volume', 'oi', 'obv'],
      },
    ],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'hma',
    label: 'HMA',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 20, low: 1, high: 500, choices: [] }],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'tema',
    label: 'TEMA',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 20, low: 1, high: 500, choices: [] }],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'rsi',
    label: 'RSI',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 14, low: 2, high: 200, choices: [] }],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'macd',
    label: 'MACD',
    family: 'library',
    params: [
      { name: 'fast', kind: 'int', default: 12, low: 1, high: 200, choices: [] },
      { name: 'slow', kind: 'int', default: 26, low: 2, high: 400, choices: [] },
      { name: 'signal', kind: 'int', default: 9, low: 1, high: 200, choices: [] },
    ],
    outs: ['macd', 'signal', 'hist'],
    tfs: null,
  },
  {
    id: 'stochastic',
    label: 'Stochastic',
    family: 'library',
    params: [
      { name: 'k', kind: 'int', default: 14, low: 1, high: 200, choices: [] },
      { name: 'd', kind: 'int', default: 3, low: 1, high: 50, choices: [] },
      { name: 'smooth', kind: 'int', default: 3, low: 1, high: 50, choices: [] },
    ],
    outs: ['k', 'd'],
    tfs: null,
  },
  {
    id: 'supertrend',
    label: 'Supertrend',
    family: 'library',
    params: [
      { name: 'atr_length', kind: 'int', default: 10, low: 1, high: 100, choices: [] },
      { name: 'factor', kind: 'dec', default: 3, low: 0.5, high: 10, choices: [] },
    ],
    outs: ['value', 'direction'],
    tfs: null,
  },
  {
    id: 'bbands',
    label: 'Bollinger Bands',
    family: 'library',
    params: [
      { name: 'length', kind: 'int', default: 20, low: 2, high: 500, choices: [] },
      { name: 'mult', kind: 'dec', default: 2, low: 0.1, high: 10, choices: [] },
    ],
    outs: ['upper', 'middle', 'lower', 'pct_b', 'width'],
    tfs: null,
  },
  {
    id: 'adx',
    label: 'ADX',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 14, low: 2, high: 100, choices: [] }],
    outs: ['adx', 'plus_di', 'minus_di'],
    tfs: null,
  },
  {
    id: 'atr',
    label: 'ATR',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 14, low: 1, high: 100, choices: [] }],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'cci',
    label: 'CCI',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 20, low: 2, high: 200, choices: [] }],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'mfi',
    label: 'MFI',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 14, low: 2, high: 200, choices: [] }],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'cmf',
    label: 'CMF',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 20, low: 2, high: 200, choices: [] }],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'williams_r',
    label: 'Williams %R',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 14, low: 2, high: 200, choices: [] }],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'donchian',
    label: 'Donchian',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 20, low: 2, high: 500, choices: [] }],
    outs: ['upper', 'lower', 'middle'],
    tfs: null,
  },
  {
    id: 'keltner',
    label: 'Keltner',
    family: 'library',
    params: [
      { name: 'length', kind: 'int', default: 20, low: 2, high: 200, choices: [] },
      { name: 'atr_length', kind: 'int', default: 10, low: 1, high: 100, choices: [] },
      { name: 'mult', kind: 'dec', default: 2, low: 0.1, high: 10, choices: [] },
    ],
    outs: ['upper', 'middle', 'lower'],
    tfs: null,
  },
  {
    id: 'rvol',
    label: 'Relative volume',
    family: 'library',
    params: [{ name: 'length', kind: 'int', default: 20, low: 2, high: 500, choices: [] }],
    outs: ['value'],
    tfs: null,
  },
  { id: 'obv', label: 'OBV', family: 'library', params: [], outs: ['value'], tfs: null },
  {
    id: 'highest',
    label: 'Highest',
    family: 'price',
    params: [
      { name: 'length', kind: 'int', default: 20, low: 1, high: 500, choices: [] },
      {
        name: 'source',
        kind: 'choice',
        default: 'high',
        low: null,
        high: null,
        choices: ['close', 'open', 'high', 'low', 'volume'],
      },
    ],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'lowest',
    label: 'Lowest',
    family: 'price',
    params: [
      { name: 'length', kind: 'int', default: 20, low: 1, high: 500, choices: [] },
      {
        name: 'source',
        kind: 'choice',
        default: 'low',
        low: null,
        high: null,
        choices: ['close', 'open', 'high', 'low', 'volume'],
      },
    ],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'orb_high',
    label: 'Opening range high',
    family: 'price',
    params: [{ name: 'minutes', kind: 'int', default: 15, low: 5, high: 60, choices: [] }],
    outs: ['value'],
    tfs: [1, 3, 5, 15, 30, 60],
  },
  {
    id: 'orb_low',
    label: 'Opening range low',
    family: 'price',
    params: [{ name: 'minutes', kind: 'int', default: 15, low: 5, high: 60, choices: [] }],
    outs: ['value'],
    tfs: [1, 3, 5, 15, 30, 60],
  },
  {
    id: 'pivot',
    label: 'Pivot',
    family: 'price',
    params: [
      {
        name: 'kind',
        kind: 'choice',
        default: 'classic',
        low: null,
        high: null,
        choices: ['classic', 'cpr'],
      },
    ],
    outs: ['P', 'R1', 'R2', 'R3', 'S1', 'S2', 'S3', 'TC', 'BC'],
    tfs: ['D'],
  },
  {
    id: 'rs',
    label: 'Relative strength',
    family: 'price',
    params: [
      {
        name: 'index',
        kind: 'choice',
        default: 'NIFTY50',
        low: null,
        high: null,
        choices: ['NIFTY50', 'NIFTYBANK'],
      },
      { name: 'n', kind: 'int', default: 20, low: 1, high: 100, choices: [] },
    ],
    outs: ['value'],
    tfs: null,
  },
  {
    id: 'pattern',
    label: 'Candle pattern',
    family: 'price',
    params: [],
    outs: ['state'],
    tfs: null,
  },
]

export const OPERATORS: OperatorInfo[] = [
  { id: 'gt', label: 'Is above', arity: 'binary', args: [] },
  { id: 'gte', label: 'Is at or above', arity: 'binary', args: [] },
  { id: 'lt', label: 'Is below', arity: 'binary', args: [] },
  { id: 'lte', label: 'Is at or below', arity: 'binary', args: [] },
  { id: 'eq', label: 'Equals', arity: 'binary', args: [] },
  { id: 'between', label: 'Is between', arity: 'unary', args: ['low', 'high'] },
  { id: 'within_pct', label: 'Is within % of', arity: 'binary', args: ['pct'] },
  { id: 'crosses_above', label: 'Crosses above', arity: 'binary', args: [] },
  { id: 'crosses_below', label: 'Crosses below', arity: 'binary', args: [] },
  { id: 'rising', label: 'Is rising for', arity: 'unary', args: ['n'] },
  { id: 'falling', label: 'Is falling for', arity: 'unary', args: ['n'] },
  { id: 'is', label: 'Is', arity: 'unary', args: ['name'] },
]

export const OPTIONS: OptionInfo[] = [
  { id: 'pcr', label: 'PCR', timed: false, underlying: true },
  { id: 'max_pain', label: 'Max pain', timed: false, underlying: true },
  { id: 'atm_iv', label: 'ATM implied volatility', timed: false, underlying: true },
  { id: 'iv_change', label: 'Change in ATM implied volatility', timed: true, underlying: true },
  { id: 'straddle', label: 'ATM straddle price', timed: false, underlying: true },
  { id: 'iv_rank', label: 'IV rank', timed: false, underlying: true },
  { id: 'iv_pct', label: 'IV percentile', timed: false, underlying: true },
  { id: 'vix_rank', label: 'India VIX rank', timed: false, underlying: false },
  { id: 'vix_pct', label: 'India VIX percentile', timed: false, underlying: false },
]

export const PATTERNS = [
  'bullish_engulfing',
  'bearish_engulfing',
  'doji',
  'hammer',
  'shooting_star',
  'inside_bar',
  'nr7',
] as const
export const BUILDUP_STATES = [
  'long_buildup',
  'short_buildup',
  'short_covering',
  'long_unwinding',
  'none',
] as const
export const INDEX_LISTS: Array<{ id: string; label: string }> = [
  { id: 'NIFTY50', label: 'NIFTY 50' },
  { id: 'NIFTYBANK', label: 'NIFTY BANK' },
  { id: 'NIFTY100', label: 'NIFTY 100' },
  { id: 'NIFTY200', label: 'NIFTY 200' },
  { id: 'NIFTY500', label: 'NIFTY 500' },
]

/** What a screen may hold (catalog.py): clauses, depth, distinct library indicators, name. */
export const MAX_CLAUSES = 20
export const MAX_DEPTH = 3
export const MAX_INSTANCES = 12
export const NAME_MAX = 60

export const TIMEFRAMES: TimeFrame[] = [1, 3, 5, 15, 30, 60, 'D', 'W']
export const RUN_TIMEFRAMES = [1, 3, 5, 15, 30, 60] as const

export const FIELD_BY_ID = new Map(FIELDS.map((field) => [field.id, field]))
export const INDICATOR_BY_ID = new Map(INDICATORS.map((indicator) => [indicator.id, indicator]))
export const OPERATOR_BY_ID = new Map(OPERATORS.map((operator) => [operator.id, operator]))
export const OPTION_BY_ID = new Map(OPTIONS.map((option) => [option.id, option]))

export const GROUP_TITLES: Record<GroupKind, string> = {
  all: 'Match all of these',
  any: 'Match any of these',
  none: 'Match none of these',
}

export type { Clause, Group, Node, Operand, Offset }
