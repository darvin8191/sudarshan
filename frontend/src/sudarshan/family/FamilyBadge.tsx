// SUDARSHAN P6 D2: the FAMILY · VIEW ONLY badge (design 6.1; rules file: `--family` #3949AB, a
// lock icon, never colour alone). It sits in the app bar of every family screen, outside what
// scrolls, so it is never out of sight. A child's reads "FAMILY · VIEW ONLY · LIMITED" so the
// member knows figures are hidden by design, not broken; on a phone narrower than 440 px the
// words are "VIEW ONLY · LIMITED" (the lock and the family colour stay, and the name read to a
// screen reader is the full one), because the long form pushed the bar past a 360 px screen.
// The words are never cut: where the row is short of room (large text on a small phone) they
// wrap to a second line inside the badge.
import { Lock } from 'lucide-react'
import { cn } from '@/lib/utils'

/** Shown from 27.5rem (440 px) up: below that a child's badge reads "VIEW ONLY · LIMITED". */
export const WIDE_ONLY = 'hidden min-[27.5rem]:inline'

export function FamilyBadge({
  limited = false,
  className,
}: {
  limited?: boolean
  className?: string
}) {
  return (
    <span
      role="img"
      aria-label={limited ? 'Family, view only, limited' : 'Family, view only'}
      data-testid="family-badge"
      className={cn(
        'inline-flex min-h-7 shrink-0 items-center gap-1 rounded-md bg-[color:var(--family)] px-2 py-0.5 text-[0.6875rem] leading-tight font-bold tracking-wide text-white',
        className
      )}
    >
      <Lock aria-hidden="true" className="size-3.5 shrink-0" />
      {/* Verification N9: the words were one line cut with an ellipsis, which at large text
          hid "VIEW ONLY" itself. They are whole words that wrap: "LIMITED" goes to a second line
          when the row is short of room, and nothing is ever cut. */}
      <span aria-hidden="true" data-part="words" className="flex min-w-0 flex-wrap gap-x-1">
        {limited ? (
          <>
            {/* Review H2: on a narrow phone the long form does not fit beside the lockup. */}
            <span data-part="family" className={cn(WIDE_ONLY, 'whitespace-nowrap')}>
              FAMILY ·{' '}
            </span>
            <span className="whitespace-nowrap">VIEW ONLY · </span>
            <span className="whitespace-nowrap">LIMITED</span>
          </>
        ) : (
          <>
            <span className="whitespace-nowrap">FAMILY · </span>
            <span className="whitespace-nowrap">VIEW ONLY</span>
          </>
        )}
      </span>
    </span>
  )
}
