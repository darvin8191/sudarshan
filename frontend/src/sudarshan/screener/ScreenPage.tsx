import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Pause, Play, Trash2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { ACTION, OUTLINED } from '@/sudarshan/books/cards/parts'
import { sendChange, useHeld } from './actions'
import { StatusChip } from './marks'
import { countsWords } from './ScreenCard'
import type { ScreenRow } from './types'
import type { ScreenTab } from './useScreenerAddress'

/**
 * One screen opened (design 5.1): its name and status, what it looks at, Pause or Resume and
 * Delete, and below them its panels. A Stopped screen is not Resumed: a stopped screen has a
 * reason the owner reads first, and edits or deletes it.
 */
export interface PanelTab {
  id: ScreenTab
  label: string
}

export function ScreenPage({
  row,
  tabs,
  tab,
  onTab,
  onBack,
  onDelete,
  children,
}: {
  row: ScreenRow
  tabs: PanelTab[]
  tab: ScreenTab
  onTab: (tab: ScreenTab) => void
  onBack: () => void
  onDelete: () => void
  children?: ReactNode
}) {
  const client = useQueryClient()
  const held = useHeld(row.id)
  const busy = held !== undefined
  const word = (action: 'pause' | 'resume') => {
    if (held?.action === action) {
      return held.state === 'sending'
        ? action === 'pause'
          ? 'Pausing'
          : 'Resuming'
        : 'Not confirmed'
    }
    return action === 'pause' ? 'Pause' : 'Resume'
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-label="Back to screens"
          onClick={onBack}
          className={cn(OUTLINED, 'w-12 shrink-0 px-0')}
        >
          <ArrowLeft aria-hidden="true" className="size-5" />
        </button>
        <h1 className="sd-display min-w-0 flex-1 truncate text-[1.5rem] leading-tight font-bold">
          {row.name}
        </h1>
      </div>
      <section className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[0.9375rem] font-semibold">{row.where}</span>
          <StatusChip status={row.status} />
        </div>
        {row.status === 'stopped' && row.status_reason && (
          <p className="text-sm font-semibold text-[color:var(--warning-text)]">
            {row.status_reason}
          </p>
        )}
        <p className="text-sm font-semibold">
          {countsWords(row)}
          {row.next_check && row.status === 'active' ? ` · ${row.next_check}` : ''}
        </p>
        <div className="flex gap-2 pt-1">
          {row.status === 'active' && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void sendChange(client, row, 'pause')}
              className={cn(OUTLINED, 'flex-1')}
            >
              <Pause aria-hidden="true" className="size-5" />
              {word('pause')}
            </button>
          )}
          {row.status === 'paused' && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void sendChange(client, row, 'resume')}
              className={cn(
                ACTION,
                'flex-1 bg-primary text-primary-foreground hover:bg-primary/90'
              )}
            >
              <Play aria-hidden="true" className="size-5" />
              {word('resume')}
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={onDelete}
            className={cn(OUTLINED, 'flex-1')}
          >
            <Trash2 aria-hidden="true" className="size-5" />
            Delete
          </button>
        </div>
      </section>
      {tabs.length > 1 && (
        <div role="tablist" aria-label="Screen" className="flex items-center gap-1.5">
          {tabs.map(({ id, label }) => (
            <button
              key={id}
              id={`sd-screen-tab-${id}`}
              type="button"
              role="tab"
              aria-selected={id === tab}
              aria-controls="sd-screen-panel"
              onClick={() => onTab(id)}
              className="group flex h-12 shrink-0 items-center outline-none"
            >
              <span
                className={cn(
                  'flex h-9 items-center rounded-full border px-3.5 text-sm group-focus-visible:ring-2 group-focus-visible:ring-ring',
                  id === tab
                    ? 'border-primary bg-[color:var(--primary-tint)] font-bold text-primary'
                    : 'border-border bg-card font-semibold text-[color:var(--text-secondary)]'
                )}
              >
                {label}
              </span>
            </button>
          ))}
        </div>
      )}
      <section
        id="sd-screen-panel"
        role="tabpanel"
        aria-labelledby={`sd-screen-tab-${tab}`}
        className="flex flex-col gap-2"
      >
        {children}
      </section>
    </div>
  )
}
