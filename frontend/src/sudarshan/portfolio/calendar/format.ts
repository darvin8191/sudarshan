// SUDARSHAN P7 F4 (M7.36): the words and icons of the corporate-action calendar. A kind is always
// an icon and its word; the per-share fact is public, the amount on what is held is the owner's.
import { CirclePlus, Coins, Gift, type LucideIcon, Split } from 'lucide-react'
import { clockOfInstant, dayOfInstant } from '../days'
import { when } from '../freshness'
import { priceText, quantityText, rupeesText } from '../tab/format'
import type { CaKind, CalendarRow, WatchlistsRead } from '../types'

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

/** Whose events the list shows (GA8.2): the owner's shares, his watchlists' shares, or both. */
export type CalendarShow = 'holdings' | 'watchlists' | 'all'
export const SHOWS: ReadonlyArray<{ id: CalendarShow; label: string }> = [
  { id: 'holdings', label: 'Holdings' },
  { id: 'watchlists', label: 'Watchlists' },
  { id: 'all', label: 'All' },
]

/** Whether a row belongs to what is shown. An answer from before GA8.1 marks no row: a holding. */
export function shownIn(row: CalendarRow, show: CalendarShow): boolean {
  const holding = row.holding !== false
  const watched = row.watchlist === true
  if (show === 'holdings') return holding
  return show === 'watchlists' ? watched : holding || watched
}

/**
 * When the watchlists' shares were read, "Watchlists as of 22 Nov, 23:50 · 12 shares": a list is
 * not market data, so it says when it was read and never "Real-time". Null before the first read.
 */
export function watchlistsAsOf(read: WatchlistsRead, today: number): string | null {
  if (read.read_at === null || read.state === 'off' || read.state === 'never') return null
  const at = `${when(dayOfInstant(read.read_at), today)}, ${clockOfInstant(read.read_at).slice(0, 5)}`
  const shares = read.share_count === 1 ? '1 share' : `${read.share_count} shares`
  return `Watchlists as of ${at} · ${shares}`
}

/**
 * What an empty list says for each choice, once Upstox's list was read. The watchlists count only
 * while their shares are shown (read, or kept from the last read); otherwise their own sentence
 * says why, and nothing claims that none of their events is listed.
 */
export function emptyText(show: CalendarShow, read: WatchlistsRead | undefined, span: string) {
  const none = 'No dividend, bonus, split or rights issue is listed'
  const lists =
    read !== undefined && (read.state === 'ok' || (read.state === 'failed' && read.share_count > 0))
  const shares = lists ? read.share_count : 0
  if (show === 'watchlists') {
    if (!lists) return null
    return shares === 0
      ? 'Your watchlists hold no shares. Futures, options and indices have no dividends, bonuses or splits of their own.'
      : `${none} for the shares on your watchlists, ${span}.`
  }
  return show === 'all' && shares > 0
    ? `${none} for what you hold or watch, ${span}.`
    : `${none} for what you hold, ${span}.`
}
