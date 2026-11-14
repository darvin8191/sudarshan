import {
  ChartCandlestick,
  FileBarChart2,
  type LucideIcon,
  NotebookPen,
  ScanSearch,
  Wrench,
} from 'lucide-react'
import { Link } from 'react-router'

/**
 * The pages a trader opens from home that the bottom bar does not hold (design 3.1, D7). The
 * phone's option chain joins here, as "Options" after Chart, in the same change as its page (O9):
 * never a tile for a page that does not exist yet.
 */
export const SHORTCUTS: ReadonlyArray<{ href: string; label: string; icon: LucideIcon }> = [
  { href: '/chart', label: 'Chart', icon: ChartCandlestick },
  { href: '/screener', label: 'Screener', icon: ScanSearch },
  { href: '/journal', label: 'Journal', icon: NotebookPen },
  { href: '/reports', label: 'Reports', icon: FileBarChart2 },
  { href: '/tools', label: 'Tools', icon: Wrench },
]

/** "Shortcuts": a tile per page, each a 48dp target with its icon and its name. */
export function Shortcuts() {
  return (
    <section
      aria-labelledby="sd-shortcuts"
      className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 pt-3 pb-3.5 shadow-[var(--shadow-card)]"
    >
      <h2 id="sd-shortcuts" className="text-[0.9375rem] font-bold">
        Shortcuts
      </h2>
      <ul className="grid grid-cols-3 gap-2">
        {SHORTCUTS.map(({ href, label, icon: Icon }) => (
          <li key={href}>
            <Link
              to={href}
              className="flex min-h-16 flex-col items-center justify-center gap-1.5 rounded-[0.875rem] border border-[color:var(--divider)] px-1 py-2 text-[0.8125rem] font-semibold outline-none hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="flex size-8 items-center justify-center rounded-[0.625rem] bg-[color:var(--brand-tint)] text-[color:var(--brand)]">
                <Icon aria-hidden="true" className="size-[1.125rem]" />
              </span>
              {label}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
