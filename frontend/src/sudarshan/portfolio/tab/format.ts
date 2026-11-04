// How an entry's figures read on the phone: paise, qty_u and price_u to rupees and units, in
// Indian grouping, with a true minus wherever a figure is negative (dashboard/format.ts).
import { formatRupees } from '@/sudarshan/dashboard/format'
import { PAISE_PER_RUPEE, PRICE_SCALE, QTY_SCALE } from '../types'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
] // biome-ignore format: one row per half year

/** 20261104 to "4 Nov 2026". */
export function dayText(day: number): string {
  const y = Math.floor(day / 10000)
  const m = Math.floor(day / 100) % 100
  return `${day % 100} ${MONTHS[m - 1] ?? '?'} ${y}`
}

/** 20261104 to its month, "November 2026", and the key that groups it. */
export function monthOf(day: number): { key: number; label: string } {
  const m = Math.floor(day / 100) % 100
  return {
    key: Math.floor(day / 100),
    label: `${MONTH_NAMES[m - 1] ?? '?'} ${Math.floor(day / 10000)}`,
  }
}

export function rupeesText(paise: number): string {
  return formatRupees(paise / PAISE_PER_RUPEE)
}

function plain(value: number, min: number, max: number): string {
  const sign = value < 0 ? '−' : ''
  return `${sign}${new Intl.NumberFormat('en-IN', { minimumFractionDigits: min, maximumFractionDigits: max }).format(Math.abs(value))}`
}

/** Units: whole numbers as they are, a fund's or a metal's decimals kept (up to six). */
export function quantityText(qtyU: number): string {
  return plain(qtyU / QTY_SCALE, 0, 6)
}

/** A price in rupees: two decimals, more only when it has them (up to four). */
export function priceText(priceU: number): string {
  return `₹${plain(priceU / PRICE_SCALE, 2, 4)}`
}
