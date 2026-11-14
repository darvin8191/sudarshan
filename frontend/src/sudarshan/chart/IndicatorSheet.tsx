/**
 * Indicators on the phone chart (M4.2, C3, C4), in a sheet: what is on the
 * chart, each with its settings and Remove; the common studies; the owner's
 * own scripts; and a search over every built-in. One set for every
 * instrument; five at most, two in a pane of their own, and a refusal past
 * either says why in a sentence.
 */
import { Plus, Search, SlidersHorizontal, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { type CatalogueEntry, COMMON_STUDIES, MAX_STUDIES, type StudyEntry } from './phoneStudies'

export interface SetRow {
  /** Its name, and why it is not drawn on this instrument, if it is not. */
  name: string
  note?: string
}

export function IndicatorSheet({
  open,
  onOpenChange,
  set,
  rows,
  derivative,
  notice,
  catalogue,
  problems,
  onAdd,
  onRemove,
  onSettings,
}: {
  open: boolean
  onOpenChange(open: boolean): void
  set: readonly StudyEntry[]
  /** One per entry of the set, in its order. */
  rows: readonly SetRow[]
  /** The instrument has open interest. */
  derivative: boolean
  /** Why the last add was refused. */
  notice: string | null
  /** Every study on offer, once the indicator tier is in; null until then. */
  catalogue: readonly CatalogueEntry[] | null
  /** The loaders' sentences for the owner's files that would not load. */
  problems: readonly string[]
  onAdd(id: string): void
  onRemove(index: number): void
  onSettings(index: number): void
}) {
  const common = COMMON_STUDIES.filter((study) => derivative || !study.derivativeOnly)
  const [query, setQuery] = useState('')
  const mine = catalogue?.filter((study) => study.mine) ?? []
  const builtIns = catalogue?.filter((study) => !study.mine) ?? []
  const words = query.trim().toLowerCase()
  const found = words
    ? builtIns
        .filter((s) => s.name.toLowerCase().includes(words) || s.id.includes(words))
        .slice(0, 40)
    : []
  return (
    <BottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title="Indicators"
      description={`For every instrument on this phone · ${set.length} of ${MAX_STUDIES}`}
    >
      {notice && (
        <output className="mb-2 block rounded-xl bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-medium text-[color:var(--warning-text)]">
          {notice}
        </output>
      )}
      <section aria-label="On this chart">
        <h3 className="pt-1 pb-1 text-xs font-semibold tracking-wide text-[color:var(--text-tertiary)] uppercase">
          On this chart
        </h3>
        {set.length === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">No indicators yet. Add one below.</p>
        ) : (
          <ul className="flex flex-col">
            {set.map((entry, index) => {
              const row = rows[index]
              return (
                <li key={`${index}:${entry.id}`} className="flex min-h-12 items-center gap-2">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[0.9375rem] font-semibold">
                      {row?.name ?? entry.id}
                    </span>
                    {row?.note && (
                      <span className="block text-xs text-muted-foreground">{row.note}</span>
                    )}
                  </span>
                  <button
                    type="button"
                    onClick={() => onSettings(index)}
                    aria-label={`Settings for ${row?.name ?? entry.id}`}
                    className="inline-flex size-12 shrink-0 items-center justify-center rounded-xl text-[color:var(--text-secondary)] hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <SlidersHorizontal aria-hidden="true" className="size-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemove(index)}
                    aria-label={`Remove ${row?.name ?? entry.id}`}
                    className="inline-flex h-12 shrink-0 items-center gap-1.5 rounded-xl px-3 text-sm font-semibold text-[color:var(--text-secondary)] hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Trash2 aria-hidden="true" className="size-4" />
                    Remove
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>
      <section aria-label="Common">
        <h3 className="pt-3 pb-1 text-xs font-semibold tracking-wide text-[color:var(--text-tertiary)] uppercase">
          Common
        </h3>
        <ul className="grid grid-cols-2 gap-x-2">
          {common.map((study) => (
            <li key={study.id}>
              <button
                type="button"
                onClick={() => onAdd(study.id)}
                aria-label={`Add ${study.label}`}
                className="flex min-h-12 w-full items-center gap-2 rounded-xl px-2 py-1 text-left text-sm leading-tight font-semibold hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Plus aria-hidden="true" className="size-4 shrink-0 text-primary" />
                <span>{study.label}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      {(mine.length > 0 || problems.length > 0) && (
        <section aria-label="My scripts">
          <h3 className={HEADING}>My scripts</h3>
          <AddList studies={mine} onAdd={onAdd} />
          {problems.map((problem) => (
            <p key={problem} className="py-1 text-xs font-medium text-[color:var(--warning-text)]">
              {problem}
            </p>
          ))}
        </section>
      )}
      <section aria-label="All indicators">
        <h3 className={HEADING}>All indicators</h3>
        <label className="relative block">
          <span className="sr-only">Search all indicators</span>
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              catalogue ? `Search ${builtIns.length} indicators` : 'Loading the indicators'
            }
            className="h-12 w-full rounded-xl border border-[color:var(--border-strong)] bg-card pr-3 pl-9 text-[0.9375rem] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        {words && found.length === 0 && (
          <p className="py-2 text-sm text-muted-foreground">No indicator by that name.</p>
        )}
        <AddList studies={found} onAdd={onAdd} />
      </section>
    </BottomSheet>
  )
}

const HEADING =
  'pt-3 pb-1 text-xs font-semibold tracking-wide text-[color:var(--text-tertiary)] uppercase'

function AddList({
  studies,
  onAdd,
}: {
  studies: readonly CatalogueEntry[]
  onAdd(id: string): void
}) {
  if (!studies.length) return null
  return (
    <ul className="flex flex-col">
      {studies.map((study) => (
        <li key={study.id}>
          <button
            type="button"
            onClick={() => onAdd(study.id)}
            aria-label={`Add ${study.name}`}
            className="flex min-h-12 w-full items-center gap-2 rounded-xl px-2 py-1 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Plus aria-hidden="true" className="size-4 shrink-0 text-primary" />
            <span className="min-w-0 flex-1 text-sm leading-tight font-semibold">{study.name}</span>
            <span className="shrink-0 text-xs text-muted-foreground">{study.category}</span>
          </button>
        </li>
      ))}
    </ul>
  )
}
