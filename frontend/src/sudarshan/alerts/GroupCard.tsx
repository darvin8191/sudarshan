import { useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Pause, Pencil, Play, Trash2 } from 'lucide-react'
import { memo, type ReactNode, useState } from 'react'
import type { AlertRow, GroupRow } from '@/api/marketAlerts'
import { cn } from '@/lib/utils'
import { CARD, Confirm, OUTLINED, SentNote } from '@/sudarshan/books/cards/parts'
import { GROUP_NO_ANSWER, sendGroupChange, useGroupHeld } from './groupActions'
import { AlertStatusChip, BusyIcon, HELD } from './lines'

/**
 * One list alert on the Alerts page (M8.14): "Swing · % move 2% · 24 symbols · Active · follows
 * the list", the service's words as they came (what it covers, what it leaves out, why it stopped
 * or froze), and Pause or Resume, Edit and Delete for the whole group. Its member alerts are
 * ordinary alert rows, shown only when the card is expanded. Delete asks first and says how many
 * alerts go.
 */
const NUMBER = new Intl.NumberFormat('en-IN')

/** "% move 2%", "Day high break", "VWAP cross": the kind and its figure, short. */
export function kindWords(group: Pick<GroupRow, 'kind' | 'params'>): string {
  const params = group.params as unknown as Record<string, string>
  if (group.kind === 'move') return `% move ${params.percent}%`
  if (group.kind === 'day_break') return params.side === 'low' ? 'Day low break' : 'Day high break'
  if (group.kind === 'vwap') return 'VWAP cross'
  return group.kind
}

const STATUS_WORDS = { active: 'Active', paused: 'Paused', stopped: 'Stopped' } as const

/** The card's one line. */
export function groupLine(group: GroupRow): string {
  const symbols = `${NUMBER.format(group.members)} ${group.members === 1 ? 'symbol' : 'symbols'}`
  return [
    group.name,
    kindWords(group),
    symbols,
    STATUS_WORDS[group.status],
    group.status === 'stopped' ? null : 'follows the list',
  ]
    .filter(Boolean)
    .join(' · ')
}

export const GroupCard = memo(function GroupCard({
  group,
  members,
  expanded,
  onToggle,
  onEdit,
  renderMember,
}: {
  group: GroupRow
  /** Its member alerts, in the order to show. */
  members: AlertRow[]
  expanded: boolean
  onToggle: (id: number) => void
  onEdit: (id: number) => void
  renderMember: (row: AlertRow) => ReactNode
}) {
  const client = useQueryClient()
  const held = useGroupHeld(group.id)
  const [confirming, setConfirming] = useState(false)
  const sending = (action: 'pause' | 'resume' | 'delete') =>
    held?.action === action && held.state === 'sending'
  const move =
    group.status === 'active'
      ? { action: 'pause' as const, label: 'Pause', busy: 'Pausing', Icon: Pause }
      : group.status === 'paused'
        ? { action: 'resume' as const, label: 'Resume', busy: 'Resuming', Icon: Play }
        : null
  const words = [group.status_reason, group.follow, ...group.left_out].filter(Boolean)
  const panel = `sd-group-${group.id}`

  return (
    <section data-group={group.id} className={cn(CARD, 'gap-2')} aria-label={groupLine(group)}>
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0 text-[0.9375rem] font-bold">{groupLine(group)}</span>
        <AlertStatusChip status={group.status} />
      </span>
      <span className="text-sm font-medium text-[color:var(--text-secondary)]">
        {group.words.condition}
      </span>
      <span className="text-xs font-medium text-[color:var(--text-tertiary)]">
        {group.words.meta}
      </span>
      {words.map((line) => (
        <span key={line} className="text-xs font-semibold text-[color:var(--text-secondary)]">
          {line}
        </span>
      ))}
      {held?.state === 'unconfirmed' && <SentNote text={GROUP_NO_ANSWER[held.action]} />}

      <div className={cn('grid gap-2', move ? 'grid-cols-3' : 'grid-cols-2')}>
        {move && (
          <button
            type="button"
            disabled={held !== undefined}
            onClick={() => void sendGroupChange(client, group, move.action)}
            className={cn(OUTLINED, 'px-2', HELD)}
          >
            {sending(move.action) ? (
              <BusyIcon />
            ) : (
              <move.Icon aria-hidden="true" className="size-5" />
            )}
            {sending(move.action) ? move.busy : move.label}
          </button>
        )}
        <button
          type="button"
          disabled={held !== undefined}
          onClick={() => onEdit(group.id)}
          className={cn(OUTLINED, 'px-2', HELD)}
        >
          <Pencil aria-hidden="true" className="size-5" />
          Edit
        </button>
        <button
          type="button"
          disabled={held !== undefined}
          onClick={() => setConfirming(true)}
          className={cn(OUTLINED, 'px-2 text-[color:var(--down)]', HELD)}
        >
          {sending('delete') ? <BusyIcon /> : <Trash2 aria-hidden="true" className="size-5" />}
          {sending('delete') ? 'Deleting' : 'Delete'}
        </button>
      </div>

      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={panel}
        onClick={() => onToggle(group.id)}
        className={cn(OUTLINED, 'justify-between')}
      >
        {expanded ? 'Hide the alerts' : `Show the alerts (${NUMBER.format(members.length)})`}
        {expanded ? (
          <ChevronUp aria-hidden="true" className="size-5" />
        ) : (
          <ChevronDown aria-hidden="true" className="size-5" />
        )}
      </button>
      {expanded && (
        <ul id={panel} className="flex flex-col gap-2">
          {members.map((row) => (
            <li key={row.id}>{renderMember(row)}</li>
          ))}
        </ul>
      )}

      <Confirm
        open={confirming}
        title={`Delete the ${group.name} list alert and its ${NUMBER.format(group.members)} ${group.members === 1 ? 'alert' : 'alerts'}?`}
        description="Their history stays."
        confirmLabel="Delete"
        onConfirm={() => void sendGroupChange(client, group, 'delete')}
        onClose={() => setConfirming(false)}
      />
    </section>
  )
})
