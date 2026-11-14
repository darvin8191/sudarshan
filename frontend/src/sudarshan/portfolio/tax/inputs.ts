// SUDARSHAN P7 F2 (M7.27): what the owner types for the tax working paper, as the rows the service
// keeps (`tax_input_set`): a loss brought forward from a filed return, the price of 31 Jan 2018 of
// an old holding, a day's trading marked as delivery, and how an account of his own counts
// (owner decision 9: "not for tax"). Every check of a value is the service's own
// (its sentence comes back); a text that is not a number goes as null and gets that sentence.
import {
  dayToInput,
  inputToDay,
  parsePrice,
  parseRupees,
  priceInput,
  rupeesInput,
} from '../sheets/parse'
import type {
  CarryBucket,
  TaxAccount,
  TaxCarry,
  TaxCarryInput,
  TaxDayOverride,
  TaxFmv,
  TaxInputs,
  TaxMode,
  TaxPack,
} from '../types'

export type InputKind = 'carry' | 'fmv' | 'day_override' | 'account_mode'
/** The kinds kept as a list of entries, each with its own "add" button. */
export type ListKind = Exclude<InputKind, 'account_mode'>
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

export function newDraft(kind: ListKind, fields: Fields = {}): Draft {
  return { kind, fields }
}

export function draftOf(kind: ListKind, row: TaxCarryInput | TaxFmv | TaxDayOverride): Draft {
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
