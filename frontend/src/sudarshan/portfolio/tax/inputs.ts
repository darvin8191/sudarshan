// SUDARSHAN P7 F2 (M7.27): what the owner types for the tax working paper, as the rows the service
// keeps (`tax_input_set`): a loss brought forward from a filed return, the price of 31 Jan 2018 of
// an old holding, a day's trading marked as delivery, a purchase from before Upstox's records, a
// buyback tender, his readings of the open rules, and how an account of his own counts (owner
// decision 9: "not for tax"). Every check of a value is the service's own
// (its sentence comes back); a text that is not a number goes as null and gets that sentence.
import {
  dayToInput,
  inputToDay,
  parsePrice,
  parseQty,
  parseRupees,
  priceInput,
  qtyInput,
  rupeesInput,
} from '../sheets/parse'
import {
  type CarryBucket,
  EXEMPT_ORDERS,
  LT_BOUNDARIES,
  type TaxAccount,
  type TaxCarry,
  type TaxCarryInput,
  type TaxDayOverride,
  type TaxFmv,
  type TaxInputs,
  type TaxMode,
  type TaxOpeningLot,
  type TaxPack,
  type TaxSettings,
  type TaxTradeMark,
  TURNOVER_METHODS,
} from '../types'

export type InputKind =
  | 'carry'
  | 'fmv'
  | 'day_override'
  | 'opening_lot'
  | 'trade_mark'
  | 'settings'
  | 'account_mode'
/** The kinds kept as a list of entries, each with its own "add" button. */
export type ListKind = Exclude<InputKind, 'settings' | 'account_mode'>
type ListRow = TaxCarryInput | TaxFmv | TaxDayOverride | TaxOpeningLot | TaxTradeMark
export type Fields = Record<string, string>
/** A form open in the sheet: a new row, or a kept one at the version it was read at. */
export interface Draft {
  kind: InputKind
  id?: number
  version?: number
  fields: Fields
}

export const KIND_WORDS: Record<ListKind, { title: string; add: string; none: string }> = {
  carry: {
    title: 'Losses brought forward',
    add: 'Add a loss from a filed return',
    none: 'None entered. Enter the losses your filed returns carry forward; yours are used over SUDARSHAN’s own figure.',
  },
  fmv: {
    title: 'Prices of 31 January 2018',
    add: 'Add a price',
    none: 'None entered. A share bought before 1 February 2018 needs its highest price of 31 January 2018.',
  },
  day_override: {
    title: 'Days counted as delivery',
    add: 'Mark a day as delivery',
    none: 'None marked. A share bought and sold on one day counts as intraday unless you mark the day here.',
  },
  opening_lot: {
    title: 'Purchases before Upstox’s records',
    add: 'Add a purchase',
    none: 'None entered. A sale whose purchase Upstox’s records do not hold has no cost here, and is left out of the totals, until you enter the purchase.',
  },
  trade_mark: {
    title: 'Buyback tenders',
    add: 'Add a buyback tender',
    none: 'None entered. A buyback is worked out by the rule of its date. Mark the sale here, or enter the tender when Upstox’s history does not list it.',
  },
}
/** The readings of the open rules: the first value of each is the usual reading, kept as "not
 * chosen" so a later change of the rule's own default reaches the paper. */
export const READINGS = [
  {
    name: 'turnover_method',
    label: 'Turnover of F&O and intraday',
    values: TURNOVER_METHODS,
    words: {
      abs_pnl: 'Sum of profits and losses, ICAI method',
      abs_pnl_plus_sale_premium: 'Sum of profits and losses plus premium on options sold',
    },
  },
  {
    name: 'exempt_order',
    label: 'Exemption and losses, when long-term gains have two rates',
    values: EXEMPT_ORDERS,
    words: {
      highest_rate_first: 'Against the gains taxed at the higher rate first',
      chronological: 'Against the earlier gains first',
    },
  },
  {
    name: 'lt_boundary',
    label: 'A share sold exactly 12 months after it was bought',
    values: LT_BOUNDARIES,
    words: { after_anniversary: 'Still short-term', on_anniversary: 'Already long-term' },
  },
] as const satisfies readonly {
  name: keyof TaxSettings
  label: string
  values: readonly string[]
  words: Record<string, string>
}[]
export const ALLOCATE = {
  label: 'Share-market charges and capital gains',
  yes: 'Shared over delivery sales and taken off the gains',
  no: 'Not taken off the gains',
} as const

/** What a reading says now: the owner's choice, or the usual one. */
export function readingWords(reading: (typeof READINGS)[number], settings: TaxSettings): string {
  const words: Record<string, string> = reading.words
  const chosen = settings[reading.name]
  return chosen && chosen !== reading.values[0]
    ? words[chosen]
    : `${words[reading.values[0]]} (the usual reading)`
}

export const BUCKET_LABELS: Record<CarryBucket, string> = {
  stcl: 'Short-term capital loss',
  ltcl: 'Long-term capital loss',
  business: 'F&O (business) loss',
  speculative: 'Intraday (speculative) loss',
}
export const MODE_WORDS: Record<TaxMode, string> = {
  reports: 'Counted from Upstox’s reports',
  ledger: 'Counted from its entries',
  none: 'Not for tax',
}

/** The form's heading for each kind. */
export const FORM_TITLES: Record<InputKind, string> = {
  carry: KIND_WORDS.carry.title,
  fmv: KIND_WORDS.fmv.title,
  day_override: KIND_WORDS.day_override.title,
  opening_lot: 'A purchase before Upstox’s records',
  trade_mark: 'A buyback tender',
  settings: 'Readings of the open rules',
  account_mode: 'How this account counts',
}
/** How an account of the owner's own can count; the Upstox account's way is not his to set. */
export const OWN_MODES = ['ledger', 'none'] as const satisfies readonly TaxMode[]

/** The form that changes how `account` counts, from the version it was read at. */
export function modeDraft(account: TaxAccount): Draft {
  return {
    kind: 'account_mode',
    id: account.id,
    version: account.version,
    fields: { account_id: String(account.id), tax_mode: account.tax_mode },
  }
}

/** The form of the readings, at the version they were read at (0 before the first save). */
export function settingsDraft(settings: TaxSettings): Draft {
  const fields: Fields = { allocate_expenses: settings.allocate_expenses ? 'yes' : 'no' }
  for (const reading of READINGS) {
    const chosen = settings[reading.name]
    fields[reading.name] = chosen && chosen !== reading.values[0] ? chosen : ''
  }
  return { kind: 'settings', version: settings.version, fields }
}

export function newDraft(kind: ListKind, fields: Fields = {}): Draft {
  return { kind, fields }
}

export function draftOf(kind: ListKind, row: ListRow): Draft {
  const fields: Fields = {}
  if (kind === 'carry') {
    const carry = row as TaxCarryInput
    Object.assign(fields, {
      loss_fy: String(carry.loss_fy),
      bucket: carry.bucket,
      amount: rupeesInput(carry.paise),
    })
  } else if (kind === 'fmv') {
    const fmv = row as TaxFmv
    Object.assign(fields, { isin: fmv.isin, price: priceInput(fmv.fmv_u) })
  } else if (kind === 'opening_lot') {
    const lot = row as TaxOpeningLot
    Object.assign(fields, {
      account_id: String(lot.account_id),
      isin: lot.isin,
      acquired_day: dayToInput(lot.acquired_day),
      qty: qtyInput(lot.qty_u),
      cost: rupeesInput(lot.cost_paise),
      note: lot.note ?? '',
    })
  } else if (kind === 'trade_mark') {
    const tender = row as TaxTradeMark
    Object.assign(fields, {
      account_id: String(tender.account_id),
      isin: tender.isin,
      day: dayToInput(tender.day),
      qty: qtyInput(tender.qty_u),
      price: priceInput(tender.price_u),
      trade_id: tender.trade_id ?? '',
    })
  } else {
    const mark = row as TaxDayOverride
    Object.assign(fields, {
      account_id: String(mark.account_id),
      isin: mark.isin,
      day: dayToInput(mark.day),
    })
  }
  return { kind, id: row.id, version: row.version, fields }
}

const whole = (text: string | undefined) => (text && /^\d+$/.test(text) ? Number(text) : null)

/** The row a draft asks the service to keep, in its own units. */
export function rowOf(draft: Draft, memberId: number): Record<string, unknown> {
  const f = draft.fields
  if (draft.kind === 'carry') {
    return {
      member_id: memberId,
      loss_fy: whole(f.loss_fy),
      bucket: f.bucket ?? null,
      paise: parseRupees(f.amount ?? ''),
      note: null,
    }
  }
  if (draft.kind === 'fmv') return { isin: f.isin ?? '', fmv_u: parsePrice(f.price ?? '') }
  if (draft.kind === 'opening_lot') {
    return {
      member_id: memberId,
      account_id: whole(f.account_id),
      isin: f.isin ?? '',
      acquired_day: inputToDay(f.acquired_day ?? ''),
      qty_u: parseQty(f.qty ?? ''),
      cost_paise: parseRupees(f.cost ?? ''),
      note: f.note?.trim() || null,
    }
  }
  if (draft.kind === 'trade_mark') {
    return {
      member_id: memberId,
      account_id: whole(f.account_id),
      isin: f.isin ?? '',
      day: inputToDay(f.day ?? ''),
      qty_u: parseQty(f.qty ?? ''),
      price_u: parsePrice(f.price ?? ''),
      trade_id: f.trade_id?.trim() || null,
    }
  }
  if (draft.kind === 'settings') {
    return {
      member_id: memberId,
      ...Object.fromEntries(READINGS.map((reading) => [reading.name, f[reading.name] || null])),
      allocate_expenses: f.allocate_expenses !== 'no',
    }
  }
  if (draft.kind === 'account_mode') {
    return {
      member_id: memberId,
      account_id: whole(f.account_id),
      tax_mode: f.tax_mode || null,
    }
  }
  return {
    member_id: memberId,
    account_id: whole(f.account_id),
    isin: f.isin ?? '',
    day: inputToDay(f.day ?? ''),
  }
}

/** SUDARSHAN's own figure for a loss the owner entered, when the paper worked one out. */
export function ownFigure(pack: TaxPack | undefined, row: TaxCarryInput): TaxCarry | null {
  const own = [...(pack?.capital_gains.own_figures ?? []), ...(pack?.business.own_figures ?? [])]
  return own.find((c) => c.loss_fy === row.loss_fy && c.bucket === row.bucket) ?? null
}

/** The shares whose sales wait for a 31 Jan 2018 price that is not entered yet. */
export function fmvWanted(pack: TaxPack | undefined, inputs: TaxInputs): string[] {
  const have = new Set(inputs.fmvs.map((row) => row.isin))
  const wanted = (pack?.capital_gains.needs_fmv ?? []).map((sale) => sale.isin)
  return [...new Set(wanted)].filter((isin) => !have.has(isin))
}

/** The accounts a day can be marked on: the ones that count for tax. */
export function countedAccounts(inputs: TaxInputs): TaxAccount[] {
  return inputs.accounts.filter((account) => account.tax_mode !== 'none')
}
