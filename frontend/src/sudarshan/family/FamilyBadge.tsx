// SUDARSHAN P6 D2: the FAMILY · VIEW ONLY badge (design 6.1; rules file: `--family` #3949AB, a
// lock icon, never colour alone). It sits in the app bar of every family screen, outside what
// scrolls, so it is never out of sight. A child's reads "FAMILY · VIEW ONLY · LIMITED" so the
// member knows figures are hidden by design, not broken.
import { Lock } from 'lucide-react'
import { cn } from '@/lib/utils'

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
        'inline-flex h-7 shrink-0 items-center gap-1 rounded-md bg-[color:var(--family)] px-2 text-[0.6875rem] leading-none font-bold tracking-wide whitespace-nowrap text-white',
        className
      )}
    >
      <Lock aria-hidden="true" className="size-3.5" />
      <span aria-hidden="true">
        {limited ? 'FAMILY · VIEW ONLY · LIMITED' : 'FAMILY · VIEW ONLY'}
      </span>
    </span>
  )
}
