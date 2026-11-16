// SUDARSHAN M13.5, M13.6 (P8 U3): what the dashboard's TRINETRA cards share: the card with its
// title and the way to the TRINETRA page, one row per reading, and the freshness line under them.
import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { BiasMark } from './BiasMark'
import { FreshnessLine } from './FreshnessLine'
import { figureName } from './ReadingCard'
import { PAGE, type Reading } from './types'
import { confidenceWords, type FreshNow } from './words'

export function SignalsCard({
  id,
  title,
  icon,
  fresh,
  children,
}: {
  id: string
  title: string
  icon: ReactNode
  fresh: FreshNow
  children: ReactNode
}) {
  return (
    <section
      aria-labelledby={id}
      data-testid={id}
      data-fresh={fresh.status}
      className={cn(
        'flex flex-col gap-1.5 rounded-[1.125rem] border bg-card px-4 pt-3 pb-3.5 shadow-[var(--shadow-card)]',
        fresh.status !== 'fresh' && 'border-[color:var(--border-strong)]'
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id={id} className="flex min-w-0 items-center gap-2 text-[0.9375rem] font-bold">
          {icon}
          <span className="min-w-0 break-words">{title}</span>
        </h2>
        <Link
          to={PAGE}
          className="-my-2 -mr-2 inline-flex min-h-12 shrink-0 items-center gap-0.5 rounded-lg px-2 text-[0.84375rem] font-semibold text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          TRINETRA
          <ChevronRight aria-hidden="true" className="size-4" />
        </Link>
      </div>
      <ul className="flex flex-col divide-y divide-[color:var(--divider)]">{children}</ul>
      <FreshnessLine fresh={fresh} compact />
    </section>
  )
}

/** One reading in a dashboard card: the index and its module, the label, the bias and its figure. */
export function ReadingRow({
  reading,
  name,
  colour,
  extra,
}: {
  reading: Reading
  name?: string
  colour: boolean
  extra?: ReactNode
}) {
  const figure = confidenceWords(reading.confidence)
  return (
    <li
      data-module={reading.module}
      data-instrument={reading.instrument}
      className="flex flex-col gap-0.5 py-2"
    >
      <span className="text-[0.75rem] font-semibold text-[color:var(--text-tertiary)]">
        {name ? `${reading.instrument} · ${name}` : reading.instrument}
      </span>
      <span className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[0.9375rem]">
        <span className="min-w-0 font-semibold break-words">{reading.label}</span>
        <BiasMark bias={reading.bias} colour={colour} />
        {figure !== null && (
          <span className="text-[0.875rem] font-semibold text-[color:var(--text-secondary)]">
            {figureName(reading.module).toLowerCase()} {figure}
          </span>
        )}
      </span>
      {extra}
    </li>
  )
}
