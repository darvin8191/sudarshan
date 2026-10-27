import { useQueryClient } from '@tanstack/react-query'
import { CandlestickChart, type LucideIcon, Pause, Play, RotateCcw, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import type { AlertRow, AlertStatus } from '@/api/marketAlerts'
import type { SymbolData } from '@/lib/MarketDataManager'
import { cn } from '@/lib/utils'
import { Confirm, Details, OUTLINED, SentNote } from '@/sudarshan/books/cards/parts'
import { chartPath } from '@/sudarshan/chart/prefs'
import type { FeedState } from '@/sudarshan/live/useFrameLines'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import { firedWords, PriceLine } from './AlertCard'
import { type AlertAction, NO_ANSWER, sendChange, useHeld } from './actions'
import { AlertStatusChip, ConditionText, LineText } from './lines'

/** The move each status allows, beside Edit and Delete (contract 1.4's button table). */
const MOVE: Record<
  AlertStatus,
  { action: Exclude<AlertAction, 'delete'>; label: string; busy: string; icon: LucideIcon }
> = {
  active: { action: 'pause', label: 'Pause', busy: 'Pausing', icon: Pause },
  paused: { action: 'resume', label: 'Resume', busy: 'Resuming', icon: Play },
  stopped: { action: 'restart', label: 'Restart', busy: 'Restarting', icon: RotateCcw },
}

export interface SheetLive {
  line: SymbolData | undefined
  followed: boolean
  feed: FeedState
  now: number
  marketOpen: boolean | null
}

/**
 * One alert's sheet (M10.3): its condition, where the price stands, why it stopped or paused,
 * its check, how often and until when, its note, and what can be done with it: Pause, Resume or
 * Restart as its status allows, Delete after a question, and its chart.
 */
export function AlertSheet({
  row,
  live,
  onClose,
}: {
  row: AlertRow | null
  live: SheetLive
  onClose: () => void
}) {
  if (!row) return null
  return <Sheet key={row.id} row={row} live={live} onClose={onClose} />
}

function Sheet({ row, live, onClose }: { row: AlertRow; live: SheetLive; onClose: () => void }) {
  const client = useQueryClient()
  const held = useHeld(row.id)
  const [confirming, setConfirming] = useState(false)
  const named = describe(row.symbol, row.exchange)
  const move = MOVE[row.status] ?? MOVE.stopped
  const MoveIcon = move.icon
  const sending = (action: AlertAction) => held?.action === action && held.state === 'sending'

  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={named.title}
      description={named.detail}
      aside={
        <div className="pt-2.5">
          <AlertStatusChip status={row.status} />
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <ConditionText row={row} className="text-base" />
        {row.words.checks_at && (
          <p className="-mt-2 pl-[1.375rem] text-sm text-[color:var(--text-secondary)]">
            {row.words.checks_at}
          </p>
        )}
        {live.followed && (
          <PriceLine
            row={row}
            line={live.line}
            feed={live.feed}
            now={live.now}
            marketOpen={live.marketOpen}
          />
        )}
        {row.status !== 'active' && row.status_reason && (
          <p className="text-sm font-semibold text-[color:var(--text-secondary)]">
            {row.status_reason}
          </p>
        )}
        {row.check && <LineText line={row.check} />}

        <Details
          rows={[
            { label: 'How often', value: row.words.meta },
            { label: 'Today', value: firedWords(row.fired_today) || 'Not fired' },
            row.note ? { label: 'Note', value: row.note } : null,
          ]}
        />

        {held?.state === 'unconfirmed' && <SentNote text={NO_ANSWER[held.action]} />}

        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={held !== undefined}
            onClick={() => void sendChange(client, row, move.action)}
            className={OUTLINED}
          >
            <MoveIcon aria-hidden="true" className="size-5" />
            {sending(move.action) ? move.busy : move.label}
          </button>
          <button
            type="button"
            disabled={held !== undefined}
            onClick={() => setConfirming(true)}
            className={cn(OUTLINED, 'text-[color:var(--down)]')}
          >
            <Trash2 aria-hidden="true" className="size-5" />
            {sending('delete') ? 'Deleting' : 'Delete'}
          </button>
        </div>
        <Link to={chartPath(row.symbol, row.exchange)} className={OUTLINED}>
          <CandlestickChart aria-hidden="true" className="size-5" />
          Chart
        </Link>
      </div>

      <Confirm
        open={confirming}
        title="Delete this alert?"
        description="Its history stays."
        confirmLabel="Delete"
        onConfirm={() => void sendChange(client, row, 'delete')}
        onClose={() => setConfirming(false)}
      />
    </BottomSheet>
  )
}
