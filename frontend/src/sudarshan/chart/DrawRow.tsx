/**
 * The drawing tools over the phone chart (M4.3, C7): a row of six tools and a
 * ruler at the top, and Undo, Cancel and Done by the thumb at the bottom right.
 *
 * A tool is placed by taps, not a drag: one for a level or a note, two (start,
 * then end) for the rest. While a tool is held one finger places and two still
 * pan and pinch. The glyphs are the chart engine's own, from its generated
 * metadata, so the draw tier itself is not loaded until a tool is first used.
 */
import { Undo2, X } from 'lucide-react'
import { useSyncExternalStore } from 'react'
import { drawToolIcon } from '@/lib/trading/drawTools'
import { cn } from '@/lib/utils'
import { OVER_CHART } from './longPress'
import { DRAW_TOOLS, type PhoneDrawings } from './phoneDrawings'

/** The row's height with its hint, which the study legend moves below. */
export const DRAW_ROW = 76

const THUMB =
  'pointer-events-auto inline-flex h-12 min-w-12 items-center justify-center gap-1.5 rounded-full border bg-card px-4 text-sm font-semibold shadow-[var(--shadow-raised)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-45'

export function DrawRow({ drawings, onDone }: { drawings: PhoneDrawings; onDone: () => void }) {
  const state = useSyncExternalStore(drawings.subscribe, drawings.state)
  const held = DRAW_TOOLS.find((tool) => tool.id === state.tool)
  const hint = state.failed
    ? 'The drawing tools could not load. Check the connection, then pick a tool again.'
    : held
      ? held.taps === 1
        ? `${held.label}: tap the chart to place it.`
        : `${held.label}: tap where it starts, then where it ends.`
      : 'Pick a tool, then tap the chart. Tap a drawing to select it.'
  return (
    <>
      <div
        className="absolute inset-x-0 top-0 z-[6] border-b bg-card/95"
        data-testid="draw-row"
        {...{ [OVER_CHART]: '' }}
      >
        <fieldset className="flex overflow-x-auto px-1">
          <legend className="sr-only">Drawing tools</legend>
          {DRAW_TOOLS.map((tool) => (
            <button
              key={tool.id}
              type="button"
              aria-label={tool.label}
              aria-pressed={state.tool === tool.id}
              onClick={() => void drawings.setTool(state.tool === tool.id ? null : tool.id)}
              className={cn(
                'inline-flex size-12 shrink-0 items-center justify-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                state.tool === tool.id
                  ? 'bg-[color:var(--primary-tint)] text-primary shadow-[inset_0_0_0_1.5px_var(--primary)]'
                  : 'text-[color:var(--text-secondary)] hover:bg-accent'
              )}
            >
              {drawToolIcon(tool.id, 20)}
            </button>
          ))}
        </fieldset>
        <output
          className={cn(
            'block truncate px-3 pb-1.5 text-xs font-medium',
            state.failed ? 'text-[color:var(--warning-text)]' : 'text-[color:var(--text-secondary)]'
          )}
        >
          {hint}
        </output>
      </div>
      <div
        className="pointer-events-none absolute right-2 bottom-8 z-[6] flex gap-2"
        {...{ [OVER_CHART]: '' }}
      >
        <button
          type="button"
          disabled={!state.canUndo}
          onClick={() => drawings.undo()}
          className={THUMB}
        >
          <Undo2 aria-hidden="true" className="size-4" />
          Undo
        </button>
        {held ? (
          <button type="button" onClick={() => drawings.cancel()} className={THUMB}>
            <X aria-hidden="true" className="size-4" />
            Cancel
          </button>
        ) : (
          <button
            type="button"
            onClick={onDone}
            className={cn(THUMB, 'border-primary text-primary')}
          >
            Done
          </button>
        )}
      </div>
    </>
  )
}
