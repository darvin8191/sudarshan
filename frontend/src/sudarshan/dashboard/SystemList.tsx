import {
  BarChart3,
  ChevronDown,
  ChevronRight,
  FileStack,
  FileText,
  Info,
  type LucideIcon,
  Search,
  Timer,
} from 'lucide-react'
import { useId, useState } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { formatAmount } from './format'
import type { MasterContractState } from './StatusRow'

/** The master contract's state in words: ready with its size, downloading, or failed. */
export function contractLine(contract: MasterContractState & { total_symbols?: number }): string {
  switch (contract.status) {
    case 'success':
      return contract.total_symbols
        ? `Ready · ${formatAmount(contract.total_symbols, 0)} symbols`
        : 'Ready'
    case 'downloading':
      return 'Downloading'
    case 'error':
      return 'Download failed · open it to try again'
    default:
      return 'Checking'
  }
}

/**
 * "System", folded until asked for (design 3.1, D7): the pages that look after the platform, in
 * place of upstream's Quick Access. Its documentation link gives way to About, and its rows say
 * what each page holds, never that something is real-time.
 */
export function SystemList({
  contract,
}: {
  contract: MasterContractState & { total_symbols?: number }
}) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const rows: Array<{ href: string; label: string; line: string; icon: LucideIcon }> = [
    {
      href: '/master-contract',
      label: 'Master contract',
      line: contractLine(contract),
      icon: FileStack,
    },
    {
      href: '/pnl-tracker',
      label: 'P&L Tracker',
      line: "The day's P&L through the session",
      icon: BarChart3,
    },
    { href: '/logs', label: 'Logs', line: 'Orders and API activity', icon: FileText },
    {
      href: '/logs/latency',
      label: 'Latency',
      line: 'How long orders and API calls take',
      icon: Timer,
    },
    { href: '/search', label: 'Symbols', line: 'Find a symbol on any exchange', icon: Search },
    { href: '/about', label: 'About', line: 'Release, build and licences', icon: Info },
  ]
  return (
    <section
      aria-labelledby={`${id}-title`}
      className="min-w-0 rounded-[1.125rem] border bg-card px-4 py-1 shadow-[var(--shadow-card)]"
    >
      <h2 id={`${id}-title`} className="text-[0.9375rem] font-bold">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`${id}-rows`}
          onClick={() => setOpen((was) => !was)}
          className="flex min-h-12 w-full items-center justify-between gap-2 rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          System
          <ChevronDown
            aria-hidden="true"
            className={cn('size-5 text-[color:var(--text-tertiary)]', open && 'rotate-180')}
          />
        </button>
      </h2>
      <ul id={`${id}-rows`} hidden={!open}>
        {rows.map(({ href, label, line, icon: Icon }) => (
          <li key={href} className="border-t border-[color:var(--divider)]">
            <Link
              to={href}
              className="flex min-h-14 items-center gap-3 rounded-lg py-2 outline-none hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="flex size-9 shrink-0 items-center justify-center rounded-[0.6875rem] bg-[color:var(--brand-tint)] text-[color:var(--brand)]">
                <Icon aria-hidden="true" className="size-[1.125rem]" />
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-px">
                <span className="text-[0.90625rem] font-semibold">{label}</span>
                <span className="text-[0.78125rem] leading-snug font-medium text-[color:var(--text-tertiary)]">
                  {line}
                </span>
              </span>
              <ChevronRight
                aria-hidden="true"
                className="size-[1.125rem] shrink-0 text-[color:var(--text-tertiary)]"
              />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
