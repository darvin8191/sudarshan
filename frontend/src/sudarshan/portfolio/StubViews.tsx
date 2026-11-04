import { cn } from '@/lib/utils'
import { CUSTOM_VIEWS, type CustomView, UPSTOX_VIEWS, type UpstoxView } from './usePortfolioOverlay'

const UPSTOX_LABELS: Record<UpstoxView, string> = {
  holdings: 'Holdings',
  positions: 'Positions',
  funds: 'Funds',
  history: 'History',
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
  overview: 'The value of this tab now, today’s change, what you put in and the profit or loss.',
  activity: 'Every entry of this tab, newest first: buys, sells, dividends, fees and transfers.',
  performance: 'Your return, how it compares with NIFTY 50 and NIFTY 500, and each financial year.',
}

const SEGMENT =
  'inline-flex min-h-12 flex-1 items-center justify-center rounded-[0.875rem] px-3 text-[0.9375rem] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

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
    <div role="tablist" aria-label="Parts of this tab" className="flex gap-1.5">
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
}: {
  view: CustomView
  onView: (view: CustomView) => void
}) {
  return (
    <div className="flex flex-col gap-3">
      <Segments views={CUSTOM_VIEWS} labels={CUSTOM_LABELS} value={view} onChange={onView} />
      <Stub view={view} label={CUSTOM_LABELS[view]} />
    </div>
  )
}
