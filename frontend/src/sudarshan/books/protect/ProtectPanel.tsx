import { ShieldCheck } from 'lucide-react'
import type { Holding } from '@/types/trading'
import { NOT_READ, protectable, protectedLine } from './protect'
import type { ProtectBook } from './useProtect'

/**
 * A holding's Protect on its sheet (M5.6, G1): what is working on it, as the GTT book says.
 * Display only: nothing is sent from here. Never colour alone: a shield and the words.
 */
export function ProtectPanel({
  holding,
  book,
}: {
  holding: Holding
  /** The holding's last price, for the sheet that sets one. */
  last: number | null
  book: ProtectBook
}) {
  if (!protectable(holding)) return null
  const protects = book.of(holding)
  if (protects.length === 0 && !book.failed) return null
  return (
    <section aria-label="Protect" className="flex flex-col gap-2">
      {protects.map((protect) => (
        <p
          key={protect.triggerId}
          data-protect="true"
          className="flex items-center gap-2 text-[0.9375rem] font-semibold text-[color:var(--info)]"
        >
          <ShieldCheck aria-hidden="true" className="size-5 shrink-0" />
          {protectedLine(protect)}
        </p>
      ))}
      {book.failed && (
        <p className="text-[0.8125rem] font-medium text-[color:var(--warning-text)]">{NOT_READ}</p>
      )}
    </section>
  )
}
