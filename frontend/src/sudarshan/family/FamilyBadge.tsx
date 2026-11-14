// SUDARSHAN P6 D2: the FAMILY · VIEW ONLY badge (design 6.1; rules file: `--family` #3949AB, a
// lock icon, never colour alone). It sits in the app bar of every family screen, outside what
// scrolls, so it is never out of sight. A child's reads "FAMILY · VIEW ONLY · LIMITED" so the
// member knows figures are hidden by design, not broken; on a phone narrower than 440 px the
// words are "VIEW ONLY · LIMITED" (the lock and the family colour stay, and the name read to a
// screen reader is the full one), because the long form pushed the bar past a 360 px screen.
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
        'inline-flex h-7 shrink-0 items-center gap-1 overflow-hidden rounded-md bg-[color:var(--family)] px-2 text-[0.6875rem] leading-none font-bold tracking-wide whitespace-nowrap text-white',
        className
      )}
    >
      <Lock aria-hidden="true" className="size-3.5 shrink-0" />
      <span aria-hidden="true" className="min-w-0 truncate">
        {limited ? (
          <>
            {/* Review H2: on a narrow phone the long form does not fit beside the lockup. */}
            <span data-part="family" className={WIDE_ONLY}>
              FAMILY ·{' '}
            </span>
            VIEW ONLY · LIMITED
          </>
        ) : (
          'FAMILY · VIEW ONLY'
        )}
      </span>
    </span>
  )
}
