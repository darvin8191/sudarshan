import { cn } from '@/lib/utils'

/**
 * The page's parts as a segmented control: each segment a 48 dp target with its word, the part
 * shown marked by the brand fill and aria-selected, never by colour alone (its word is bold and
 * the selected segment has an inset edge). Used by /journal and /reports.
 *
 * v0.17.0 phone review L1: the segments share the width equally while their words fit, and a
 * segment whose word needs more takes it (`minmax(min-content, 1fr)`), so a word never runs past
 * its segment ("Watchlists" at 320 px with Larger text); below 360 px with Larger text their side
 * padding narrows (`sd-seg`, theme.css).
 */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: ReadonlyArray<{ id: T; label: string }>
  onChange: (id: T) => void
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className="grid min-h-12 gap-1 rounded-[0.875rem] border bg-card p-1 shadow-[var(--shadow-card)]"
      style={{ gridTemplateColumns: `repeat(${options.length}, minmax(min-content, 1fr))` }}
    >
      {options.map((option) => {
        const selected = option.id === value
        return (
          <button
            key={option.id}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(option.id)}
            className={cn(
              'sd-seg min-h-12 rounded-[0.625rem] px-2 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
              selected
                ? 'bg-[color:var(--brand)] font-bold text-white'
                : 'text-[color:var(--text-secondary)] hover:bg-accent'
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
