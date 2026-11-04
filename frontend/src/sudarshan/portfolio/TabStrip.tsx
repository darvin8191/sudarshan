import {
  Briefcase,
  ChartLine,
  Coins,
  Gem,
  House,
  Landmark,
  Lock,
  type LucideIcon,
  Plus,
  Target,
  Wallet,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { MAX_TABS, type TabIcon, type TabInfo, WORDS } from './types'

const ICONS: Record<TabIcon, LucideIcon> = {
  briefcase: Briefcase,
  chart: ChartLine,
  landmark: Landmark,
  coins: Coins,
  home: House,
  gem: Gem,
  wallet: Wallet,
  target: Target,
}

const CHIP =
  'inline-flex min-h-12 shrink-0 items-center gap-2 rounded-full border px-4 text-[0.9375rem] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
// The selected chip has the brand tint, a 2 px brand edge and bold text: not colour alone.
const SELECTED =
  'border-2 border-[color:var(--brand)] bg-[color:var(--brand-tint)] font-bold text-[color:var(--brand)]'
const PLAIN =
  'border-[color:var(--border-strong)] bg-card font-medium text-[color:var(--text-secondary)] hover:bg-accent'

/**
 * The tabs of the portfolio as a strip that scrolls sideways: Upstox first (read-only, with a
 * lock and the word), then the owner's tabs in order, each with its icon and name, then
 * "New tab". The selected one is told by tint, edge and weight, and by `aria-selected`.
 */
export function TabStrip({
  tabs,
  selected,
  onSelect,
  onNew,
}: {
  /** The custom tabs to show, in order (archived ones are left out by the caller). */
  tabs: readonly TabInfo[]
  /** The custom tab shown; null is the Upstox tab. */
  selected: number | null
  onSelect: (tab: number | null) => void
  onNew: () => void
}) {
  const full = tabs.length >= MAX_TABS
  return (
    <div
      role="tablist"
      aria-label="Portfolio tabs"
      className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 md:mx-0 md:px-0"
    >
      <button
        type="button"
        role="tab"
        aria-selected={selected === null}
        onClick={() => onSelect(null)}
        className={cn(CHIP, selected === null ? SELECTED : PLAIN)}
      >
        <Lock aria-hidden="true" className="size-4" />
        <span>Upstox</span>
        <span className="text-xs font-medium">read-only</span>
      </button>
      {tabs.map((tab) => {
        const Icon = ICONS[tab.icon] ?? Briefcase
        const on = selected === tab.id
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onSelect(tab.id)}
            className={cn(CHIP, on ? SELECTED : PLAIN)}
          >
            <Icon aria-hidden="true" className="size-4" />
            <span className="max-w-[10rem] truncate">{tab.name}</span>
          </button>
        )
      })}
      <button
        type="button"
        onClick={onNew}
        disabled={full}
        title={full ? WORDS.tab_limit : undefined}
        aria-describedby={full ? 'portfolio-tab-limit' : undefined}
        className={cn(
          CHIP,
          'border-dashed border-[color:var(--border-strong)] bg-card font-semibold text-[color:var(--brand)] hover:bg-accent disabled:opacity-50'
        )}
      >
        <Plus aria-hidden="true" className="size-4" />
        New tab
      </button>
      {full && (
        <span id="portfolio-tab-limit" className="sr-only">
          {WORDS.tab_limit}
        </span>
      )}
    </div>
  )
}
