// SUDARSHAN M7.35: the filters of a tab (design 4.8): account, class, tag, held over or under a
// year, and as of a date. The service applies them (tab_get); a filter in force is told by a chip
// with its words and an X, and "Clear filters" takes them all off. As of a date, quantities and
// cost are those of that day and the value is at that day's prices.
import { Check, ChevronDown, Filter, X } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { dayToInput, inputToDay } from '../sheets/parse'
import { CHOICE, OFF, ON } from '../sheets/TabFields'
import {
  type Account,
  ASSET_CLASS_LABELS,
  type Asset,
  type AssetClass,
  type Filters,
  type HeldFilter,
} from '../types'
import { dayText } from './format'

export const NO_FILTERS: Filters = { accounts: [], classes: [], tags: [], held: null, as_of: null }

export function countFilters(f: Filters): number {
  return (
    (f.accounts.length > 0 ? 1 : 0) +
    (f.classes.length > 0 ? 1 : 0) +
    (f.tags.length > 0 ? 1 : 0) +
    (f.held ? 1 : 0) +
    (f.as_of ? 1 : 0)
  )
}

const HELD_WORDS: Record<HeldFilter, string> = {
  over: 'Held over a year',
  under: 'Held under a year',
}

function toggle<T>(list: readonly T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className="text-sm font-semibold text-[color:var(--text-secondary)]">{title}</legend>
      <div className="flex flex-wrap gap-2">{children}</div>
    </fieldset>
  )
}

function Pick({
  on,
  onClick,
  children,
}: {
  on: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick} className={cn(CHOICE, on ? ON : OFF)}>
      {on && <Check aria-hidden="true" className="size-4" />}
      {children}
    </button>
  )
}

/** The words of each filter in force, for the chips under the button. */
export function activeWords(
  f: Filters,
  accounts: readonly Account[]
): Array<{ key: string; words: string; clear: Partial<Filters> }> {
  const out: Array<{ key: string; words: string; clear: Partial<Filters> }> = []
  if (f.accounts.length > 0) {
    const names = f.accounts.map((id) => accounts.find((a) => a.id === id)?.name ?? 'Account')
    out.push({ key: 'accounts', words: names.join(', '), clear: { accounts: [] } })
  }
  if (f.classes.length > 0) {
    out.push({
      key: 'classes',
      words: f.classes.map((c) => ASSET_CLASS_LABELS[c]).join(', '),
      clear: { classes: [] },
    })
  }
  if (f.tags.length > 0) out.push({ key: 'tags', words: f.tags.join(', '), clear: { tags: [] } })
  if (f.held) out.push({ key: 'held', words: HELD_WORDS[f.held], clear: { held: null } })
  if (f.as_of)
    out.push({ key: 'as_of', words: `As of ${dayText(f.as_of)}`, clear: { as_of: null } })
  return out
}

export function FilterBar({
  filters,
  onChange,
  accounts,
  assets,
  today,
}: {
  filters: Filters
  onChange: (next: Filters) => void
  accounts: readonly Account[]
  /** Every asset the tab names: the classes and tags to choose from stay the same while filtered. */
  assets: readonly Asset[]
  today: number
}) {
  const [open, setOpen] = useState(false)
  const count = countFilters(filters)
  const own = accounts.filter((a) => !a.system)
  const classes = [...new Set(assets.map((a) => a.asset_class))] as AssetClass[]
  const tags = [...new Set(assets.flatMap((a) => a.tags))].sort()
  const patch = (p: Partial<Filters>) => onChange({ ...filters, ...p })
  const active = activeWords(filters, accounts)

  return (
    <section aria-label="Filters" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="inline-flex min-h-12 items-center gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-3 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <Filter aria-hidden="true" className="size-4" />
          Filters{count > 0 ? ` · ${count}` : ''}
          <ChevronDown aria-hidden="true" className={cn('size-4', open && 'rotate-180')} />
        </button>
        {active.map((a) => (
          <button
            key={a.key}
            type="button"
            aria-label={`Remove filter: ${a.words}`}
            onClick={() => patch(a.clear)}
            className="inline-flex min-h-12 items-center gap-1.5 rounded-full border-2 border-[color:var(--brand)] bg-[color:var(--brand-tint)] px-3 text-sm font-bold text-[color:var(--brand)] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            {a.words}
            <X aria-hidden="true" className="size-4" />
          </button>
        ))}
        {count > 0 && (
          <button
            type="button"
            onClick={() => onChange(NO_FILTERS)}
            className="min-h-12 px-2 text-sm font-semibold text-primary"
          >
            Clear filters
          </button>
        )}
      </div>
      {open && (
        <div className="flex flex-col gap-4 rounded-[1.125rem] border bg-card p-4 shadow-[var(--shadow-card)]">
          {own.length > 0 && (
            <Group title="Account">
              {own.map((a) => (
                <Pick
                  key={a.id}
                  on={filters.accounts.includes(a.id)}
                  onClick={() => patch({ accounts: toggle(filters.accounts, a.id) })}
                >
                  {a.name}
                </Pick>
              ))}
            </Group>
          )}
          {classes.length > 0 && (
            <Group title="Class">
              {classes.map((c) => (
                <Pick
                  key={c}
                  on={filters.classes.includes(c)}
                  onClick={() => patch({ classes: toggle(filters.classes, c) })}
                >
                  {ASSET_CLASS_LABELS[c]}
                </Pick>
              ))}
            </Group>
          )}
          {tags.length > 0 && (
            <Group title="Tag">
              {tags.map((t) => (
                <Pick
                  key={t}
                  on={filters.tags.includes(t)}
                  onClick={() => patch({ tags: toggle(filters.tags, t) })}
                >
                  {t}
                </Pick>
              ))}
            </Group>
          )}
          <Group title="Held">
            <Pick on={filters.held === null} onClick={() => patch({ held: null })}>
              Any time
            </Pick>
            {(['over', 'under'] as const).map((h) => (
              <Pick key={h} on={filters.held === h} onClick={() => patch({ held: h })}>
                {HELD_WORDS[h]}
              </Pick>
            ))}
          </Group>
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="sd-as-of"
              className="text-sm font-semibold text-[color:var(--text-secondary)]"
            >
              As of a date
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <input
                id="sd-as-of"
                type="date"
                max={dayToInput(today)}
                value={filters.as_of ? dayToInput(filters.as_of) : ''}
                onChange={(event) => {
                  const day = inputToDay(event.target.value)
                  patch({ as_of: day !== null && day <= today ? day : null })
                }}
                className="min-h-12 rounded-[0.875rem] border border-input bg-card px-3 text-base outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
              />
              {filters.as_of && (
                <button
                  type="button"
                  className="min-h-12 px-2 text-sm font-semibold text-primary"
                  onClick={() => patch({ as_of: null })}
                >
                  Back to today
                </button>
              )}
            </div>
            <p className="text-xs text-[color:var(--text-tertiary)]">
              Quantities and cost as they were on that day, valued at that day’s prices.
            </p>
          </div>
        </div>
      )}
    </section>
  )
}
