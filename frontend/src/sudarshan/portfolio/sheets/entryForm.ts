/**
 * The Add sheet's form, as text, and the entry it makes (design 4.2 and 4.10), free of React.
 * `buildEntry` says either what to send or, in words, what is still missing or wrong; a form that
 * is not whole is never sent for a dry run. On screen "Opening holding", "Add a valuable" and
 * "Loan" are modes of their own; stored they are a transfer from outside, a buy of a manual asset
 * and a liability asset.
 */

import { valuePaise } from '../money'
import {
  type Activity,
  type ActivityInput,
  type ActivityKind,
  type Asset,
  CHARGE_PARTS,
  type ChargePart,
  type ChargesDetail,
  QTY_SCALE,
  WORDS,
} from '../types'
import {
  dayToInput,
  inputToDay,
  parsePrice,
  parseQty,
  parseRupees,
  parseWhole,
  priceInput,
  qtyInput,
  rupeesInput,
} from './parse'

export const MODES = [
  'BUY',
  'SELL',
  'DIVIDEND',
  'FEE',
  'INTEREST',
  'TRANSFER',
  'OPENING',
  'SPLIT',
  'BONUS',
  'RIGHTS',
  'VALUABLE',
  'LOAN',
] as const
export type Mode = (typeof MODES)[number]
export const MAIN_MODES: readonly Mode[] = ['BUY', 'SELL', 'DIVIDEND']
export const MORE_MODES: readonly Mode[] = MODES.filter((m) => !MAIN_MODES.includes(m))

export const MODE_LABELS: Record<Mode, string> = {
  BUY: 'Buy',
  SELL: 'Sell',
  DIVIDEND: 'Dividend',
  FEE: 'Fee',
  INTEREST: 'Interest',
  TRANSFER: 'Transfer',
  OPENING: 'Opening holding',
  SPLIT: 'Split',
  BONUS: 'Bonus',
  RIGHTS: 'Rights',
  VALUABLE: 'Add a valuable',
  LOAN: 'Loan',
}

/** The asset an entry names, with what the sheet needs to know about it. */
export interface AssetPick {
  asset: Asset
  /** The last price known for it (a fund's NAV, the tab's price), price_u, to prefill. */
  priceU: number | null
}

export interface Form {
  mode: Mode
  asset: AssetPick | null
  accountId: number | null
  toAccountId: number | null
  /** TRANSFER: the units come from outside, or go to outside. */
  fromOutside: boolean
  toOutside: boolean
  date: string
  qty: string
  price: string
  amount: string
  /** The owner has typed the amount: it is no longer worked out from quantity x price. */
  amountTouched: boolean
  charges: string
  parts: Record<ChargePart, string>
  useParts: boolean
  tds: string
  value: string
  lotDate: string
  ratioNum: string
  ratioDen: string
  note: string
  /** LOAN. */
  loanName: string
  /**
   * An edited transfer from outside or opening holding: the quantity and price as loaded and the
   * stored amount and value. They stand unless the owner changes the quantity or the price, so an
   * imported row whose value differed from quantity x price is not rewritten by opening it.
   */
  stored: {
    qty: string
    price: string
    amount_paise: number | null
    value_paise: number | null
  } | null
}

const NO_PARTS = Object.fromEntries(CHARGE_PARTS.map((p) => [p, ''])) as Record<ChargePart, string>

export function emptyForm(mode: Mode, today: number, accountId: number | null): Form {
  return {
    mode,
    asset: null,
    accountId,
    toAccountId: null,
    fromOutside: false,
    toOutside: false,
    date: dayToInput(today),
    qty: '',
    price: '',
    amount: '',
    amountTouched: false,
    charges: '',
    parts: { ...NO_PARTS },
    useParts: false,
    tds: '',
    value: '',
    lotDate: '',
    ratioNum: '',
    ratioDen: '1',
    note: '',
    loanName: '',
    stored: null,
  }
}

/** The amount of a trade while the owner has not typed one: quantity x price. */
export function workedAmount(form: Form): string {
  const qty = parseQty(form.qty)
  const price = parsePrice(form.price)
  if (qty === null || price === null || qty === 0) return ''
  return rupeesInput(valuePaise(qty, price))
}

/** The charges in paise: the total typed, or the sum of the parts when the breakdown is on. */
export function chargesOf(form: Form): number | null {
  if (!form.useParts) return form.charges.trim() === '' ? 0 : parseRupees(form.charges)
  let sum = 0
  for (const part of CHARGE_PARTS) {
    const text = form.parts[part].trim()
    if (text === '') continue
    const paise = parseRupees(text)
    if (paise === null) return null
    sum += paise
  }
  return sum
}

export type Built =
  | { ok: true; entry: ActivityInput; draft: boolean; future: boolean }
  | { ok: false; words: string; problem: boolean }

const missing = (words: string): Built => ({ ok: false, words, problem: false })
const wrong = (words: string): Built => ({ ok: false, words, problem: true })

/** Whether the typed quantity has a fraction. */
const hasFraction = (qtyU: number) => qtyU % QTY_SCALE !== 0

/**
 * `keepDraft`: the entry being edited is a draft whose date has come. It stays a draft (not
 * counted) unless the owner chooses to count it, so editing it never posts it silently.
 */
export function buildEntry(form: Form, today: number, keepDraft = false): Built {
  const mode = form.mode
  if (mode === 'LOAN') return missing('A loan is saved on its own.')
  const day = inputToDay(form.date)
  if (day === null) return missing('Choose the date.')
  const future = day > today
  const draft = future || keepDraft
  const asset = form.asset?.asset ?? null
  const qty = parseQty(form.qty)
  const needsAsset = mode !== 'FEE' && mode !== 'INTEREST'
  if (needsAsset && !asset) return missing('Choose what this is for.')
  const needsAccount = mode !== 'SPLIT' && mode !== 'TRANSFER' && mode !== 'OPENING'
  if (needsAccount && form.accountId === null) return missing('Choose the account.')

  const common = { trade_date: day, status: draft ? ('draft' as const) : ('posted' as const) }
  const note = form.note.trim() ? { note: form.note.trim() } : {}
  const done = (entry: ActivityInput): Built => ({
    ok: true,
    entry: { ...common, ...note, ...entry },
    draft,
    future,
  })

  if (mode === 'SPLIT') {
    const num = parseWhole(form.ratioNum)
    const den = parseWhole(form.ratioDen)
    if (num === null || den === null) return missing('Say how many new shares for how many old.')
    return done({
      kind: 'SPLIT',
      trade_date: day,
      account_id: form.accountId,
      asset_id: asset?.id ?? null,
      ratio_num: num,
      ratio_den: den,
    })
  }

  if (mode === 'DIVIDEND' || mode === 'INTEREST' || mode === 'FEE') {
    const amount = parseRupees(form.amount)
    if (amount === null) return missing('Enter the amount.')
    const tds = form.tds.trim() === '' ? 0 : parseRupees(form.tds)
    if (tds === null) return wrong('The tax deducted is not an amount.')
    return done({
      kind: mode as ActivityKind,
      trade_date: day,
      account_id: form.accountId,
      asset_id: asset?.id ?? null,
      amount_paise: amount,
      ...(mode === 'FEE' ? {} : { tds_paise: tds }),
    })
  }

  // The rest move units.
  if (qty === null || qty === 0) return missing('Enter the quantity.')
  if (asset?.kind === 'listed' && hasFraction(qty)) return wrong(WORDS.whole_shares)

  if (mode === 'BONUS') {
    return done({
      kind: 'BONUS',
      trade_date: day,
      account_id: form.accountId,
      asset_id: asset?.id ?? null,
      qty_u: qty,
    })
  }

  if (mode === 'BUY' || mode === 'SELL' || mode === 'RIGHTS' || mode === 'VALUABLE') {
    const price = form.price.trim() === '' ? null : parsePrice(form.price)
    if (form.price.trim() !== '' && price === null) return wrong('The price is not a number.')
    const text = form.amountTouched ? form.amount : workedAmount(form) || form.amount
    const amount = parseRupees(text)
    if (amount === null) return missing('Enter the amount, or the price.')
    const charges = chargesOf(form)
    if (charges === null) return wrong('The charges are not an amount.')
    return done({
      kind: mode === 'VALUABLE' ? 'BUY' : (mode as ActivityKind),
      trade_date: day,
      account_id: form.accountId,
      asset_id: asset?.id ?? null,
      qty_u: qty,
      price_u: price,
      amount_paise: amount,
      charges_paise: charges,
      charges_detail: form.useParts ? detailOf(form) : null,
    })
  }

  // TRANSFER and OPENING.
  const cost = form.price.trim() === '' ? null : parsePrice(form.price)
  if (mode === 'OPENING') {
    if (form.accountId === null) return missing('Choose the account that holds it.')
    if (cost === null) return missing('Enter the cost per unit.')
    const kept = unchanged(form)
    const total = valuePaise(qty, cost)
    return done({
      kind: 'TRANSFER',
      trade_date: day,
      account_id: null,
      to_account_id: form.accountId,
      asset_id: asset?.id ?? null,
      qty_u: qty,
      price_u: cost,
      amount_paise: kept?.amount_paise ?? total,
      value_paise: kept?.value_paise ?? total,
      lot_date: day,
    })
  }
  const from = form.fromOutside ? null : form.accountId
  const to = form.toOutside ? null : form.toAccountId
  if (!form.fromOutside && from === null) return missing('Choose the account it leaves.')
  if (!form.toOutside && to === null) return missing('Choose the account it reaches.')
  if (form.fromOutside && form.toOutside) return wrong('A transfer needs an account on one side.')
  if (from !== null && from === to) return wrong('Choose two different accounts.')
  const outside = form.fromOutside || form.toOutside
  const value = outside ? parseRupees(form.value) : null
  if (outside && value === null) return missing('Enter the value on that day.')
  if (form.fromOutside && cost === null) return missing('Enter the cost per unit.')
  const lot = form.fromOutside ? (inputToDay(form.lotDate) ?? day) : null
  return done({
    kind: 'TRANSFER',
    trade_date: day,
    account_id: from,
    to_account_id: to,
    asset_id: asset?.id ?? null,
    qty_u: qty,
    ...(form.fromOutside && cost !== null
      ? {
          price_u: cost,
          amount_paise: unchanged(form)?.amount_paise ?? valuePaise(qty, cost),
        }
      : {}),
    ...(value !== null ? { value_paise: value } : {}),
    ...(lot !== null ? { lot_date: lot } : {}),
  })
}

/** The stored amount and value of an edited transfer, while its quantity and price are as loaded. */
function unchanged(form: Form): { amount_paise: number | null; value_paise: number | null } | null {
  const kept = form.stored
  if (!kept || kept.qty !== form.qty.trim() || kept.price !== form.price.trim()) return null
  return { amount_paise: kept.amount_paise, value_paise: kept.value_paise }
}

function detailOf(form: Form): ChargesDetail | null {
  const out: ChargesDetail = {}
  for (const part of CHARGE_PARTS) {
    const paise = form.parts[part].trim() === '' ? null : parseRupees(form.parts[part])
    if (paise) out[`${part}_paise`] = paise
  }
  return Object.keys(out).length > 0 ? out : null
}

/** The form an existing entry fills in, for Edit and Duplicate. */
export function formOf(entry: Activity, assets: readonly Asset[], today: number): Form {
  const asset = assets.find((a) => a.id === entry.asset_id) ?? null
  const opening =
    entry.kind === 'TRANSFER' &&
    entry.account_id === null &&
    entry.to_account_id !== null &&
    entry.lot_date === entry.trade_date
  const mode: Mode = opening ? 'OPENING' : (entry.kind as Mode)
  const form = emptyForm(mode, entry.trade_date || today, entry.account_id)
  const detail = entry.charges_detail
  return {
    ...form,
    asset: asset ? { asset, priceU: null } : null,
    accountId: opening ? entry.to_account_id : entry.account_id,
    toAccountId: entry.kind === 'TRANSFER' ? entry.to_account_id : null,
    fromOutside: entry.kind === 'TRANSFER' && entry.account_id === null,
    toOutside: entry.kind === 'TRANSFER' && entry.to_account_id === null,
    qty: entry.qty_u === null ? '' : qtyInput(entry.qty_u),
    price: entry.price_u === null ? '' : priceInput(entry.price_u),
    amount: entry.amount_paise === null ? '' : rupeesInput(entry.amount_paise),
    amountTouched: entry.amount_paise !== null,
    charges: entry.charges_paise ? rupeesInput(entry.charges_paise) : '',
    useParts: detail !== null && Object.keys(detail).length > 0,
    parts: {
      ...NO_PARTS,
      ...Object.fromEntries(
        CHARGE_PARTS.flatMap((p) => {
          const paise = detail?.[`${p}_paise`]
          return paise ? [[p, rupeesInput(paise)]] : []
        })
      ),
    },
    tds: entry.tds_paise ? rupeesInput(entry.tds_paise) : '',
    value: entry.value_paise === null ? '' : rupeesInput(entry.value_paise),
    lotDate: entry.lot_date ? dayToInput(entry.lot_date) : '',
    ratioNum: entry.ratio_num ? String(entry.ratio_num) : '',
    ratioDen: entry.ratio_den ? String(entry.ratio_den) : '1',
    note: entry.note ?? '',
    stored:
      entry.kind === 'TRANSFER' && entry.account_id === null
        ? {
            qty: entry.qty_u === null ? '' : qtyInput(entry.qty_u),
            price: entry.price_u === null ? '' : priceInput(entry.price_u),
            amount_paise: entry.amount_paise,
            value_paise: entry.value_paise,
          }
        : null,
  }
}
