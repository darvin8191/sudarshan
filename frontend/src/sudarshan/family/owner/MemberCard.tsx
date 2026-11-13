// SUDARSHAN P6 D7a (M20.6): one member of the family on the owner's page. The whole card is one
// 48 dp button that opens the member's sheet. Every state is a word and an icon, never a colour
// alone: a role chip (icon and word), a status chip (Active, Invited · expires 18:40, Locked
// until 19:05, Suspended), the devices, the privacy in one line, Telegram, last seen.
import {
  Baby,
  ChevronRight,
  CircleCheck,
  CirclePause,
  Clock,
  Eye,
  Lock,
  Send,
  Smartphone,
} from 'lucide-react'
import type { ReactNode } from 'react'
import type { MemberCard as Card } from '../types'
import { Chip } from './parts'
import {
  devicesLine,
  lastSeenLine,
  privacyLine,
  ROLE_WORDS,
  type StatusKind,
  statusOf,
} from './words'

const STATUS_ICON: Record<StatusKind, ReactNode> = {
  active: <CircleCheck className="size-3.5" />,
  invited: <Clock className="size-3.5" />,
  locked: <Lock className="size-3.5" />,
  suspended: <CirclePause className="size-3.5" />,
  removed: <CirclePause className="size-3.5" />,
}
const STATUS_TONE: Record<StatusKind, 'good' | 'warn' | 'bad' | 'plain'> = {
  active: 'good',
  invited: 'warn',
  locked: 'bad',
  suspended: 'plain',
  removed: 'plain',
}

export function MemberCard({
  card,
  now,
  onOpen,
}: {
  card: Card
  now: number
  onOpen: (id: number) => void
}) {
  const status = statusOf(card, now)
  const role = ROLE_WORDS[card.role]
  return (
    <button
      type="button"
      onClick={() => onOpen(card.id)}
      className="flex min-h-12 w-full flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-3.5 text-left shadow-[var(--shadow-card)] outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="flex items-start gap-2">
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-[1.0625rem] font-bold">{card.display_name}</span>
          <span className="truncate text-[0.8125rem] text-[color:var(--text-tertiary)]">
            {card.handle ? `Sign-in name ${card.handle}` : 'Not signed in yet'}
          </span>
        </span>
        <ChevronRight
          aria-hidden="true"
          className="mt-1 size-5 shrink-0 text-[color:var(--text-tertiary)]"
        />
      </span>
      <span className="flex flex-wrap gap-1.5">
        <Chip
          tone="brand"
          icon={
            card.role === 'child' ? <Baby className="size-3.5" /> : <Eye className="size-3.5" />
          }
        >
          {role.label}
        </Chip>
        <Chip tone={STATUS_TONE[status.kind]} icon={STATUS_ICON[status.kind]}>
          {status.label}
        </Chip>
      </span>
      <span className="text-[0.875rem] text-[color:var(--text-secondary)]">
        {privacyLine(card.privacy)}
      </span>
      <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.8125rem] text-[color:var(--text-tertiary)]">
        <span className="inline-flex items-center gap-1">
          <Smartphone aria-hidden="true" className="size-3.5" />
          {devicesLine(card.devices)}
        </span>
        <span className="inline-flex items-center gap-1">
          <Send aria-hidden="true" className="size-3.5" />
          {card.telegram_linked ? 'Telegram linked' : 'Telegram not linked'}
        </span>
        <span>{lastSeenLine(card.last_seen_at, now)}</span>
      </span>
    </button>
  )
}
