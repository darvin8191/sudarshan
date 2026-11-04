import {
  ArrowDownRight,
  ArrowDownToLine,
  ArrowUpDown,
  ArrowUpRight,
  ArrowUpToLine,
  Ban,
  BellRing,
  CalendarX2,
  CircleStop,
  Clock,
  Eye,
  Gavel,
  Hourglass,
  Info,
  LoaderCircle,
  type LucideIcon,
  Moon,
  Pause,
  PowerOff,
  TrendingDown,
  TrendingUp,
  WifiOff,
} from 'lucide-react'
import type {
  AlertRow,
  AlertStatus,
  BreakParams,
  LineCode,
  MoveParams,
  PriceParams,
  StatusLine,
  VwapParams,
} from '@/api/marketAlerts'
import { cn } from '@/lib/utils'

/**
 * The marks the Alerts page draws (M10.3): a status line with an icon for its code, an alert's
 * status chip, and the direction of its condition. Never colour alone: every one has its icon
 * and its words, and the words are the server's wherever the server sends them.
 */

const LINE_ICON: Record<LineCode, LucideIcon> = {
  ok: Eye,
  closing_auction: Gavel,
  feed_down: WifiOff,
  not_open_yet: Clock,
  closed_by_exchange: Ban,
  outside_hours: Moon,
  closed_today: CalendarX2,
  waiting: Hourglass,
  note: Info,
  starting: LoaderCircle,
  off: PowerOff,
}

/**
 * A button that cannot be pressed while a change is on its way. A faded one read at 2.4:1 to
 * 3.4:1, too little in sunlight; this keeps its words at full contrast on white, grey with a
 * dashed edge. The one being sent carries `BusyIcon` and its busy word too.
 */
export const HELD =
  'disabled:cursor-not-allowed disabled:border disabled:border-dashed disabled:border-[color:var(--border-strong)] disabled:bg-card disabled:text-[color:var(--text-secondary)] disabled:opacity-100 disabled:shadow-none'

/** The turning mark beside a busy word: "Saving", "Pausing". */
export function BusyIcon() {
  return <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
}

const LINE_TONE: Partial<Record<LineCode, string>> = {
  ok: 'text-[color:var(--live-ink)]',
  feed_down: 'text-[color:var(--warning-text)]',
  off: 'text-[color:var(--warning-text)]',
  closing_auction: 'text-[color:var(--warning-text)]',
  waiting: 'text-[color:var(--info)]',
  note: 'text-[color:var(--info)]',
}

/** A status line from the server: its icon by code, its words as they came. */
export function LineText({ line, className }: { line: StatusLine; className?: string }) {
  const Icon = LINE_ICON[line.code] ?? Info
  return (
    <span
      data-line={line.code}
      className={cn(
        'inline-flex items-start gap-1.5 text-[0.8125rem] font-semibold',
        LINE_TONE[line.code] ?? 'text-[color:var(--text-secondary)]',
        className
      )}
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>{line.line}</span>
    </span>
  )
}

const STATUS: Record<AlertStatus, { label: string; icon: LucideIcon; className: string }> = {
  active: {
    label: 'Active',
    icon: BellRing,
    className: 'bg-[color:var(--brand-tint)] text-[color:var(--brand)]',
  },
  paused: {
    label: 'Paused',
    icon: Pause,
    className: 'bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]',
  },
  stopped: {
    label: 'Stopped',
    icon: CircleStop,
    className: 'bg-[color:var(--surface-2)] text-[color:var(--neutral)]',
  },
}

/** Active, Paused or Stopped: its word and icon, on a tint. */
export function AlertStatusChip({ status }: { status: AlertStatus }) {
  const look = STATUS[status] ?? STATUS.stopped
  const Icon = look.icon
  return (
    <span
      data-status={status}
      className={cn(
        'inline-flex h-6 shrink-0 items-center gap-1 rounded-md px-2 text-[0.71875rem] leading-none font-bold tracking-wide',
        look.className
      )}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {look.label}
    </span>
  )
}

type Way = 'up' | 'down' | 'either'

/** Which way an alert's condition looks, and the arrow that says so. */
function wayOf(row: Pick<AlertRow, 'kind' | 'params'>): { way: Way; icon: LucideIcon } {
  switch (row.kind) {
    case 'price': {
      const { op } = row.params as PriceParams
      if (op === 'crosses_above') return { way: 'up', icon: ArrowUpRight }
      if (op === 'at_or_above') return { way: 'up', icon: ArrowUpToLine }
      if (op === 'crosses_below') return { way: 'down', icon: ArrowDownRight }
      if (op === 'at_or_below') return { way: 'down', icon: ArrowDownToLine }
      return { way: 'either', icon: ArrowUpDown }
    }
    case 'move': {
      const { direction } = row.params as MoveParams
      if (direction === 'up') return { way: 'up', icon: TrendingUp }
      if (direction === 'down') return { way: 'down', icon: TrendingDown }
      return { way: 'either', icon: ArrowUpDown }
    }
    case 'day_break':
      return (row.params as BreakParams).side === 'low'
        ? { way: 'down', icon: ArrowDownToLine }
        : { way: 'up', icon: ArrowUpToLine }
    default: {
      const { direction } = row.params as VwapParams
      if (direction === 'above') return { way: 'up', icon: ArrowUpRight }
      if (direction === 'below') return { way: 'down', icon: ArrowDownRight }
      return { way: 'either', icon: ArrowUpDown }
    }
  }
}

const WAY_TONE: Record<Way, string> = {
  up: 'text-[color:var(--up)]',
  down: 'text-[color:var(--down)]',
  either: 'text-[color:var(--text-secondary)]',
}

/** The condition in the server's words, after the arrow of its direction. */
export function ConditionText({
  row,
  className,
}: {
  row: Pick<AlertRow, 'kind' | 'params' | 'words'>
  className?: string
}) {
  const { way, icon: Icon } = wayOf(row)
  return (
    <span data-way={way} className={cn('inline-flex items-start gap-1.5 font-semibold', className)}>
      <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', WAY_TONE[way])} />
      <span>{row.words.condition}</span>
    </span>
  )
}
