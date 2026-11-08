import {
  CircleCheck,
  CircleStop,
  Clock,
  Flame,
  Hourglass,
  Info,
  LoaderCircle,
  type LucideIcon,
  Moon,
  Pause,
  PowerOff,
  WifiOff,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { EngineLine, EngineState, ScreenStatus } from './types'

/**
 * The marks the Screener page draws (M9.3): a status chip with its icon and word, and the engine
 * line with an icon for its state. Never colour alone, and the words are the server's wherever
 * the server sends them.
 */

const ENGINE_ICON: Record<EngineState, LucideIcon> = {
  real_time: CircleCheck,
  closed: Moon,
  feed_down: WifiOff,
  auction: Clock,
  warming_up: LoaderCircle,
  no_prices: Hourglass,
  waiting_master: Hourglass,
  switched_off: PowerOff,
  not_answering: WifiOff,
}

const ENGINE_TONE: Partial<Record<EngineState, string>> = {
  real_time: 'text-[color:var(--live-ink)]',
  feed_down: 'text-[color:var(--warning-text)]',
  not_answering: 'text-[color:var(--warning-text)]',
  switched_off: 'text-[color:var(--warning-text)]',
  no_prices: 'text-[color:var(--warning-text)]',
  auction: 'text-[color:var(--warning-text)]',
}

/** The engine line: its icon by state, its words as they came; grey when it is not current. */
export function EngineText({ line, className }: { line: EngineLine; className?: string }) {
  const Icon = ENGINE_ICON[line.state] ?? Info
  return (
    <span
      data-engine={line.state}
      className={cn(
        'inline-flex items-start gap-1.5 text-[0.8125rem] font-semibold',
        ENGINE_TONE[line.state] ?? 'text-[color:var(--text-secondary)]',
        className
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn('mt-0.5 size-4 shrink-0', line.state === 'warming_up' && 'animate-spin')}
      />
      <span>{line.line}</span>
    </span>
  )
}

const STATUS: Record<ScreenStatus, { label: string; icon: LucideIcon; className: string }> = {
  active: {
    label: 'Active',
    icon: Flame,
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
export function StatusChip({ status }: { status: ScreenStatus }) {
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
