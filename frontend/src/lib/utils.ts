import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
// SUDARSHAN M20.3: rupees grouped the way Display settings ask (Indian unless changed).
import { numberLocale } from '@/sudarshan/dashboard/numberStyle'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Sanitize a value for CSV export to prevent formula injection.
 * Prefixes dangerous characters (=, +, -, @) with a single quote.
 */
export function sanitizeCSV(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return ''
  const str = String(value)
  // Prefix dangerous formula characters with a single quote
  if (/^[=+\-@]/.test(str)) {
    return `'${str}`
  }
  // Escape quotes and wrap in quotes if contains comma
  if (str.includes(',') || str.includes('"') || str.includes('\n')) {
    return `"${str.replace(/"/g, '""')}"`
  }
  return str
}

/**
 * Returns a currency formatter bound to the active broker.
 * - deltaexchange → USD ($)
 * - all other brokers  → INR (₹)
 */
export function makeFormatCurrency(broker?: string | null): (value: number) => string {
  const isUSD = broker === 'deltaexchange'
  return (value: number) =>
    isUSD
      ? new Intl.NumberFormat('en-US', {
          style: 'currency',
          currency: 'USD',
          minimumFractionDigits: 2,
        }).format(value)
      : new Intl.NumberFormat(numberLocale(), {
          style: 'currency',
          currency: 'INR',
          minimumFractionDigits: 2,
        }).format(value)
}
