/**
 * What a selected drawing offers (M4.3, C8), in a bar low on the chart, above
 * Undo and Done, so the drawing itself stays in view: its ink, Lock, Delete, and for a note
 * its words. Three inks, each with a line style of its own (solid, dashed,
 * dotted), so two drawings differ without colour. Delete can be undone from
 * the Draw row. A locked drawing no longer answers a tap, so the way back is
 * "Unlock", shown while any drawing is locked.
 */
import { Lock, Pencil, Trash2, X } from 'lucide-react'
import { useSyncExternalStore } from 'react'
import { cn } from '@/lib/utils'
import { OVER_CHART } from './longPress'
import { DRAW_INKS, type PhoneDrawings } from './phoneDrawings'

const KEY =
  'inline-flex size-12 shrink-0 items-center justify-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring hover:bg-accent'
const DASH: Record<string, string | undefined> = {
  solid: undefined,
  dashed: '5 3',
  dotted: '1.5 3',
}

export function DrawingSheet({
  drawings,
  onEditNote,
}: {
  drawings: PhoneDrawings
  onEditNote: (id: string) => void
}) {
  const state = useSyncExternalStore(drawings.subscribe, drawings.state)
  // While a tool is held the chart is for its taps: nothing of this is in their way.
  if (state.tool) return null
  const selected = state.selected
  if (!selected)
    return state.locked > 0 ? (
      <button
        type="button"
        onClick={() => drawings.unlockAll()}
        className="absolute bottom-[5.75rem] left-2 z-[6] inline-flex h-12 items-center gap-1.5 rounded-full border bg-card px-4 text-sm font-semibold shadow-[var(--shadow-raised)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        {...{ [OVER_CHART]: '' }}
      >
        <Lock aria-hidden="true" className="size-4" />
        Unlock {state.locked} locked
      </button>
    ) : null
  return (
    <section
      aria-label={`${selected.label} selected`}
      className="absolute inset-x-2 bottom-[5.75rem] z-[7] sm:right-auto flex items-center rounded-full border bg-card px-1 shadow-[var(--shadow-raised)]"
      data-testid="drawing-bar"
      {...{ [OVER_CHART]: '' }}
    >
      <fieldset className="flex">
        <legend className="sr-only">Ink</legend>
        {DRAW_INKS.map((ink, index) => (
          <button
            key={ink.name}
            type="button"
            aria-label={`${ink.name}, ${ink.lineStyle}`}
            aria-pressed={selected.ink === index}
            onClick={() => drawings.setInk(index)}
            className={cn(
              KEY,
              selected.ink === index && 'shadow-[inset_0_0_0_1.5px_var(--primary)]'
            )}
          >
            <svg viewBox="0 0 24 8" width="24" height="8" aria-hidden="true" focusable="false">
              <line
                x1="1"
                y1="4"
                x2="23"
                y2="4"
                stroke={`var(${ink.token})`}
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeDasharray={DASH[ink.lineStyle]}
              />
            </svg>
          </button>
        ))}
      </fieldset>
      <span className="mx-1 h-6 w-px bg-border" aria-hidden="true" />
      {selected.tool === 'text' && (
        <button
          type="button"
          aria-label="Edit the note's words"
          onClick={() => onEditNote(selected.id)}
          className={KEY}
        >
          <Pencil aria-hidden="true" className="size-4" />
        </button>
      )}
      <button
        type="button"
        aria-label="Lock where it is"
        onClick={() => drawings.lockSelected()}
        className={KEY}
      >
        <Lock aria-hidden="true" className="size-4" />
      </button>
      <button
        type="button"
        aria-label={`Delete this ${selected.label.toLowerCase()}`}
        onClick={() => drawings.removeSelected()}
        className={cn(KEY, 'text-destructive')}
      >
        <Trash2 aria-hidden="true" className="size-4" />
      </button>
      <button
        type="button"
        aria-label="Done with this drawing"
        onClick={() => drawings.deselect()}
        className={cn(KEY, 'ml-auto')}
      >
        <X aria-hidden="true" className="size-4" />
      </button>
    </section>
  )
}
