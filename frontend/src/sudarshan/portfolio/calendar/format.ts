// SUDARSHAN P7 F4 (M7.36): the words and icons of the corporate-action calendar. A kind is always
// an icon and its word; the per-share fact is public, the amount on what is held is the owner's.
import { CirclePlus, Coins, Gift, type LucideIcon, Split } from 'lucide-react'
import { dayOfInstant } from '../days'
import { when } from '../freshness'
import { priceText, quantityText, rupeesText } from '../tab/format'
import type { CaKind, CalendarRow } from '../types'

export const KINDS: Record<CaKind, { word: string; icon: LucideIcon }> = {
  dividend: { word: 'Dividend', icon: Coins },
  bonus: { word: 'Bonus', icon: Gift },
  split: { word: 'Split', icon: Split },
  rights: { word: 'Rights', icon: CirclePlus },
}

/** The public fact of an event: "₹5.50 a share", or the ratio as Upstox wrote it. */
export function factText(row: Pick<CalendarRow, 'kind' | 'amount_u' | 'ratio_num' | 'ratio_den'>) {
  if (row.amount_u !== null) return `${priceText(row.amount_u)} a share`
  if (row.ratio_num !== null && row.ratio_den !== null) {
    return `Ratio ${row.ratio_num}:${row.ratio_den}, as Upstox lists it`
  }
  return row.kind === 'dividend' ? 'Amount not given' : 'Ratio not given'
}

/** What it means for the owner: "You hold 300: about ₹1,650.00"; null when the share is not held. */
export function holdText(row: CalendarRow, today: number): string | null {
  if (row.held_qty_u === null) return null
  const verb = row.ex_day > today ? 'You hold' : 'You held'
  const held = `${verb} ${quantityText(row.held_qty_u)}`
  return row.expected_paise === null ? held : `${held}: about ${rupeesText(row.expected_paise)}`
}

export function checkedText(row: CalendarRow, today: number): string {
  return row.checked_at === null
    ? 'Not checked yet'
    : `Checked ${when(dayOfInstant(row.checked_at), today)}`
}
