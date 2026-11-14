import { CalendarDays, type LucideIcon, Receipt, ShieldCheck, Target } from 'lucide-react'
import { cn } from '@/lib/utils'
import { type SwitchName, WORDS } from './types'
import { CUSTOM_VIEWS, type CustomView, UPSTOX_VIEWS, type UpstoxView } from './usePortfolioOverlay'

export const UPSTOX_LABELS: Record<UpstoxView, string> = {
  holdings: 'Holdings',
  positions: 'Positions',
  funds: 'Funds',
  history: 'History',
  trades: 'Trades',
  pnl: 'P&L',
}
const CUSTOM_LABELS: Record<CustomView, string> = {
  overview: 'Overview',
  holdings: 'Holdings',
  activity: 'Activity',
  performance: 'Performance',
}

/** What each part will show, in a sentence. The parts themselves arrive one at a time. */
export const STUB_WORDS: Record<UpstoxView | CustomView, string> = {
  holdings:
    'Every holding with its quantity, average price, price now, today’s change and profit or loss.',
  positions: 'Your open positions in Upstox, for reading only. Orders are placed in Positions.',
  funds: 'Your Upstox funds, line by line, with the time they were read.',
  history: 'The value of your Upstox account against what you put in, then the last 10 days.',
  trades: 'Every trade Upstox lists for a financial year, with its date, side, quantity and price.',
  pnl: 'Your realised profit or loss for a financial year, trade by trade, with the charges.',
  overview: 'The value of this tab now, today’s change, what you put in and the profit or loss.',
  activity: 'Every entry of this tab, newest first: buys, sells, dividends, fees and transfers.',
  performance: 'Your return, how it compares with NIFTY 50 and NIFTY 500, and each financial year.',
}

const SEGMENT =
  'inline-flex min-h-12 flex-1 items-center justify-center rounded-[0.875rem] px-3 text-[0.9375rem] whitespace-nowrap outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

/** The parts of a tab as a row of segments; the shown one is told by tint, edge and weight. */
export function Segments<V extends string>({
  views,
  labels,
  value,
  onChange,
}: {
  views: readonly V[]
  labels: Record<V, string>
  value: V
  onChange: (view: V) => void
}) {
  return (
    <div role="tablist" aria-label="Parts of this tab" className="flex gap-1.5 overflow-x-auto">
      {views.map((view) => (
        <button
          key={view}
          type="button"
          role="tab"
          aria-selected={value === view}
          onClick={() => onChange(view)}
          className={cn(
            SEGMENT,
            value === view
              ? 'border-2 border-[color:var(--brand)] bg-[color:var(--brand-tint)] font-bold text-[color:var(--brand)]'
              : 'border border-[color:var(--border-strong)] bg-card font-medium text-[color:var(--text-secondary)] hover:bg-accent'
          )}
        >
          {labels[view]}
        </button>
      ))}
    </div>
  )
}

function Stub({ view, label }: { view: UpstoxView | CustomView; label: string }) {
  return (
    <section
      aria-label={label}
      className="rounded-[1.125rem] border bg-card px-4 py-6 text-center shadow-[var(--shadow-card)]"
    >
      <h2 className="text-base font-bold">{label}</h2>
      <p className="mx-auto mt-1 max-w-sm text-sm text-[color:var(--text-secondary)]">
        {STUB_WORDS[view]}
      </p>
      <p className="mx-auto mt-2 max-w-sm text-sm text-[color:var(--text-tertiary)]">
        This part of the portfolio is not ready yet.
      </p>
    </section>
  )
}

export function UpstoxStub({
  view,
  onView,
}: {
  view: UpstoxView
  onView: (view: UpstoxView) => void
}) {
  return (
    <div className="flex flex-col gap-3">
      <Segments views={UPSTOX_VIEWS} labels={UPSTOX_LABELS} value={view} onChange={onView} />
      <Stub view={view} label={UPSTOX_LABELS[view]} />
    </div>
  )
}

export function CustomStub({
  view,
  onView,
  hideBody = false,
}: {
  view: CustomView
  onView: (view: CustomView) => void
  /** The part has its own screen: only the segments are drawn. */
  hideBody?: boolean
}) {
  return (
    <div className="flex flex-col gap-3">
      <Segments views={CUSTOM_VIEWS} labels={CUSTOM_LABELS} value={view} onChange={onView} />
      {!hideBody && <Stub view={view} label={CUSTOM_LABELS[view]} />}
    </div>
  )
}

// ---- SUDARSHAN P7 F0 (M7.27): the money views beside the tabs ----
// Each is `/portfolio?view=<name>` and is offered only while the service says its switch is on
// (`state.switches`); off, the chip is not drawn and the address falls back to the tabs. Each
// view's own screen replaces its stub here when its part lands.

/** The views of phase P7 that stand beside the tabs, in the order the chips show them. */
export const PHASE_VIEWS = ['tax', 'risk', 'calendar', 'goals'] as const
export type PhaseView = (typeof PHASE_VIEWS)[number]

/** The switch each view waits for (the service's `SWITCHED_OPS`). */
export const PHASE_SWITCH: Record<PhaseView, SwitchName> = {
  tax: 'tax',
  risk: 'insights',
  calendar: 'insights',
  goals: 'goals',
}

export const PHASE_LABELS: Record<PhaseView, string> = {
  tax: 'Tax',
  risk: 'Risk',
  calendar: 'Calendar',
  goals: 'Goals',
}

const PHASE_ICONS: Record<PhaseView, LucideIcon> = {
  tax: Receipt,
  risk: ShieldCheck,
  calendar: CalendarDays,
  goals: Target,
}

/** What each view will show, in a sentence. */
export const PHASE_WORDS: Record<PhaseView, string> = {
  tax: 'A working paper of your capital gains, F&O income, losses carried forward and charges for a financial year, to verify with your CA.',
  risk: 'How far your portfolio fell from its high, how much it swings, and where it is concentrated.',
  calendar: 'Dividends, bonuses and splits of what you hold, and what each means for you.',
  goals: 'Your goals, how far along each one is, and what to add each month to reach it.',
}

/** The views whose switch is on, in chip order; a switch the service did not name is off. */
export function phaseViewsOn(switches: Partial<Record<SwitchName, boolean>>): PhaseView[] {
  return PHASE_VIEWS.filter((view) => switches[PHASE_SWITCH[view]] === true)
}

/** The view of phase P7 the address names, when it is on; null for anything else. */
export function phaseViewOf(params: URLSearchParams, on: readonly PhaseView[]): PhaseView | null {
  const view = params.get('view')
  return on.find((name) => name === view) ?? null
}

const PHASE_CHIP =
  'inline-flex min-h-12 shrink-0 items-center gap-2 rounded-full border px-4 text-[0.9375rem] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

/**
 * The views of phase P7 that are on, as chips beside the tabs: each with its icon and its word,
 * the one open told by tint, edge, weight and `aria-selected`, never by colour alone.
 */
export function PhaseChips({
  views,
  selected,
  onSelect,
}: {
  views: readonly PhaseView[]
  selected: PhaseView | null
  onSelect: (view: PhaseView) => void
}) {
  if (views.length === 0) return null
  return (
    <div
      role="tablist"
      aria-label="Money views"
      className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 md:mx-0 md:px-0"
    >
      {views.map((view) => {
        const Icon = PHASE_ICONS[view]
        const on = selected === view
        return (
          <button
            key={view}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onSelect(view)}
            className={cn(
              PHASE_CHIP,
              on
                ? 'border-2 border-[color:var(--brand)] bg-[color:var(--brand-tint)] font-bold text-[color:var(--brand)]'
                : 'border-[color:var(--border-strong)] bg-card font-medium text-[color:var(--text-secondary)] hover:bg-accent'
            )}
          >
            <Icon aria-hidden="true" className="size-4" />
            <span>{PHASE_LABELS[view]}</span>
          </button>
        )
      })}
    </div>
  )
}

/** A view of phase P7 until its screen lands: its title, what it will show, and that it is not
 *  ready yet. Not market data, so nothing here is live and nothing says "Real-time". */
export function PhaseStub({ view }: { view: PhaseView }) {
  const Icon = PHASE_ICONS[view]
  return (
    <section
      aria-label={PHASE_LABELS[view]}
      className="rounded-[1.125rem] border bg-card px-4 py-6 text-center shadow-[var(--shadow-card)]"
    >
      <div className="mx-auto mb-2 flex size-10 items-center justify-center text-[color:var(--brand)]">
        <Icon aria-hidden="true" className="size-6" />
      </div>
      <h2 className="sd-display text-[1.375rem] leading-tight font-bold">{PHASE_LABELS[view]}</h2>
      <p className="mx-auto mt-1 max-w-sm text-sm text-[color:var(--text-secondary)]">
        {PHASE_WORDS[view]}
      </p>
      <p className="mx-auto mt-2 max-w-sm text-sm text-[color:var(--text-tertiary)]">
        {WORDS.not_ready}
      </p>
    </section>
  )
}
