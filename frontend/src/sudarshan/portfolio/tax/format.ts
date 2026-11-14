// SUDARSHAN P7 F1 (M7.27): how the tax working paper's figures and times read on the phone. The
// service works every figure out (sudarshan/tax); nothing here adds, nets or rounds one.
import { dateTimeOfInstant } from '../days'
import { rupeesText } from '../tab/format'
import type { TaxPack } from '../types'

export const rs = rupeesText

/** The financial year (the year it starts in) an IST day falls in. */
export function fyOfDay(day: number): number {
  const year = Math.floor(day / 10_000)
  return Math.floor(day / 100) % 100 >= 4 ? year : year - 1
}

/** 2026 to "FY 2026-27". */
export function fyLabel(fy: number): string {
  return `FY ${fy}-${String((fy + 1) % 100).padStart(2, '0')}`
}

/** A rate in basis points as the paper names it: 1250 to "12.5%". */
export function rateText(bp: number): string {
  return `${bp / 100}%`
}

/** How long ago an instant was, in the largest whole unit: "3 h ago". */
export function agoText(thenMs: number, nowMs: number): string {
  const minutes = Math.max(0, Math.floor((nowMs - thenMs) / 60_000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  if (minutes < 48 * 60) return `${Math.floor(minutes / 60)} h ago`
  return `${Math.floor(minutes / 1440)} days ago`
}

/** Past this, a running year's read of Upstox is called old (the reader runs every night). */
export const TAX_OLD_MS = 36 * 3_600_000

/**
 * When the paper was worked out and how old its oldest read of Upstox is. Tax is not market data:
 * the line never says "Real-time", and `old` asks for the clock icon and the word beside it.
 */
export function asOf(pack: TaxPack, nowMs: number): { words: string; old: boolean } {
  const reads = pack.inputs.sources.flatMap((s) => (s.read_at === null ? [] : [s.read_at]))
  const oldest = reads.length > 0 ? Math.min(...reads) : null
  const parts = [`As of ${dateTimeOfInstant(pack.as_of_ms).slice(0, -3)}`]
  if (pack.running) parts.push('Year to date')
  if (oldest !== null) parts.push(`read from Upstox ${agoText(oldest, nowMs)}`)
  return {
    words: parts.join(' · '),
    old: pack.running && oldest !== null && nowMs - oldest > TAX_OLD_MS,
  }
}
