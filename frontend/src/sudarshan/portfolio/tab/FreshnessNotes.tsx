// SUDARSHAN M7.39: widget 9 of the Overview (design 4.6): the values on this tab that are old (a
// hand-kept value past the tab's limit, a NAV AMFI has not refreshed), each with an Update button
// that opens the price sheet. "Old" is a clock and the word, never a colour alone.
import { Clock } from 'lucide-react'
import type { PriceTarget } from '../sheets/PriceSheet'
import type { Asset, FreshnessNote } from '../types'

const TILE = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
const LABEL = 'text-xs font-semibold text-muted-foreground'

/** The price sheet's target for a note, from the tab's assets (the unit of a manual asset). */
export function targetOf(
  note: FreshnessNote,
  assets: readonly Asset[],
  tabId: number,
  tabVersion: number
): PriceTarget {
  const asset = assets.find((a) => a.id === note.asset_id)
  return {
    asset_id: note.asset_id,
    name: note.name,
    kind: note.kind === 'nav' ? 'mf' : 'manual',
    unit_label: asset?.unit_label ?? null,
    tab_id: tabId,
    tab_version: tabVersion,
  }
}

export function FreshnessNotes({
  notes,
  onUpdate,
}: {
  notes: readonly FreshnessNote[]
  onUpdate: (note: FreshnessNote) => void
}) {
  return (
    <section aria-label="Notes on prices" className={`${TILE} flex flex-col gap-2`}>
      <h2 className={LABEL}>Notes on prices</h2>
      {notes.length === 0 ? (
        <p className="text-sm text-[color:var(--text-secondary)]">
          Every value on this tab is current.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
          {notes.map((note) => (
            <li key={`${note.kind}-${note.asset_id}`} className="flex items-center gap-3 py-1.5">
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-[0.9375rem] font-semibold">{note.name}</span>
                <span className="inline-flex items-center gap-1 text-xs font-semibold text-[color:var(--warning-text)]">
                  <Clock aria-hidden="true" className="size-3.5 shrink-0" />
                  Old · {note.words}
                </span>
              </div>
              <button
                type="button"
                onClick={() => onUpdate(note)}
                aria-label={`Update ${note.name}`}
                className="inline-flex min-h-12 min-w-12 items-center justify-center rounded-[0.875rem] border border-[color:var(--border-strong)] px-3 text-sm font-semibold text-[color:var(--brand)] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                Update
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
