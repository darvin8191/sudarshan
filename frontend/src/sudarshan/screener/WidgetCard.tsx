import {
  ChevronRight,
  CircleCheck,
  CircleStop,
  Clock,
  Hourglass,
  List,
  LoaderCircle,
  type LucideIcon,
  Moon,
  Pause,
  RefreshCw,
  WifiOff,
} from 'lucide-react'
import { memo } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { OUTLINED } from '@/sudarshan/books/cards/parts'
import { keyOf } from '@/sudarshan/dashboard/live'
import { rememberOpenList } from '@/sudarshan/watchlist/openList'
import type { LiveRow } from '@/sudarshan/watchlist/useLiveRows'
import type { LineIcon, LineView } from './cardLine'
import { cardKey } from './dashboardCache'
import { StatusChip } from './marks'
import { ResultLine, runWords } from './Results'
import type { WidgetCard as Card, ScreenStatus } from './types'

const ICONS: Record<LineIcon, LucideIcon> = {
  'wifi-off': WifiOff,
  'circle-stop': CircleStop,
  pause: Pause,
  hourglass: Hourglass,
  moon: Moon,
  clock: Clock,
  'circle-check': CircleCheck,
  loader: LoaderCircle,
}

/** The card's line: an icon and the words, in the warning ink when the card cannot be live. */
function Line({ line }: { line: LineView }) {
  const Icon = ICONS[line.icon]
  return (
    <p
      data-card-line={line.icon}
      className={cn(
        'flex items-start gap-1.5 text-[0.8125rem] font-semibold',
        line.warn
          ? 'text-[color:var(--warning-text)]'
          : line.icon === 'circle-check'
            ? 'text-[color:var(--live-ink)]'
            : 'text-[color:var(--text-secondary)]'
      )}
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>{line.words}</span>
    </p>
  )
}

/** Two day-only choices of "when it checks", as the chip under the name. */
function ModeChip({ mode }: { mode: Card['mode'] }) {
  if (mode === 'intraday') return null
  const forming = mode === 'forming_day'
  const Icon = forming ? Hourglass : Clock
  return (
    <span
      data-mode={mode}
      className={cn(
        'inline-flex h-6 items-center gap-1 rounded-md px-2 text-[0.71875rem] font-bold',
        forming
          ? 'bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]'
          : 'bg-[color:var(--divider)] text-[color:var(--text-secondary)]'
      )}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {forming ? 'Forming day candle' : 'After the close'}
    </span>
  )
}

const FOOT =
  'flex min-h-12 w-full items-center justify-between gap-2 px-4 text-left text-[0.875rem] font-semibold text-primary outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring'

/**
 * One table widget (design 3.6): the screen's name and status, where and when it looks, the card's
 * line, its rows (at least 56 dp, each one button that opens the symbol sheet) with the price and
 * the change ticking for the rows the page follows and the price at the run for the others, and
 * a foot: "and N more" and the screen's automatic list, each 48 dp. The order of the rows changes
 * only at a check: ticks change the figures in place.
 */
export const WidgetCard = memo(function WidgetCard({
  card,
  name,
  where,
  status,
  line,
  liveByKey,
  now,
  unreadable,
  onRetry,
  onOpenScreen,
  onOpenRow,
}: {
  card: Card
  name: string
  where: string
  status: ScreenStatus
  line: LineView
  liveByKey: ReadonlyMap<string, LiveRow>
  now: number
  /** The read for this card failed. */
  unreadable: boolean
  onRetry: () => void
  onOpenScreen: (id: number) => void
  onOpenRow: (card: Card, key: string) => void
}) {
  const list = card.list
  return (
    <li
      data-widget={cardKey(card)}
      aria-label={`${name}, ${where}`}
      className="flex flex-col overflow-hidden rounded-[1rem] border bg-card shadow-[var(--shadow-card)]"
    >
      <button
        type="button"
        onClick={() => onOpenScreen(card.screen_id)}
        aria-label={`${name}: open this screen`}
        className="flex min-h-12 flex-col gap-1 px-4 pt-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="flex w-full items-start justify-between gap-2">
          <span className="min-w-0 truncate text-base font-bold">{name}</span>
          <StatusChip status={status} />
        </span>
        <span className="flex flex-wrap items-center gap-2 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          {where}
          <ModeChip mode={card.mode} />
        </span>
      </button>
      <div className="flex flex-col gap-1 px-4 pt-1 pb-2">
        <Line line={line} />
        <p
          data-run={card.run?.id}
          className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]"
        >
          {card.run
            ? `${runWords(card.run)} · ${card.matches ?? card.run.matches} match`
            : status === 'active'
              ? 'Not checked yet.'
              : 'Not checking.'}
        </p>
      </div>
      {card.rows_pending ? (
        <div className="flex min-h-14 items-center gap-2 border-t px-4 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
          {unreadable ? (
            <>
              <span className="flex-1">This card could not be read.</span>
              <button type="button" onClick={onRetry} className={OUTLINED}>
                <RefreshCw aria-hidden="true" className="size-4" />
                Try again
              </button>
            </>
          ) : (
            <>
              <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
              Reading the matches
            </>
          )}
        </div>
      ) : card.rows_shown.length === 0 ? (
        card.waits === null &&
        card.run && (
          <p className="border-t px-4 py-3 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
            Nothing matches at the last check
          </p>
        )
      ) : (
        <ul
          data-greyed={line.greyed || undefined}
          className={cn('divide-y border-t', line.greyed && 'opacity-60')}
        >
          {card.rows_shown.map((row) => {
            const key = keyOf(row.symbol, row.exchange)
            return (
              <ResultLine
                key={row.key ?? key}
                row={row}
                live={liveByKey.get(key)}
                runAt={card.run?.at ?? 0}
                now={now}
                onOpen={(open) => onOpenRow(card, open)}
              />
            )
          })}
        </ul>
      )}
      {(card.more > 0 || list?.id) && (
        <div className="flex flex-col border-t">
          {card.more > 0 && (
            <button type="button" onClick={() => onOpenScreen(card.screen_id)} className={FOOT}>
              and {card.more} more
              <ChevronRight aria-hidden="true" className="size-4" />
            </button>
          )}
          {list?.id && (
            <Link
              to="/watchlist"
              onClick={() => rememberOpenList(list.id as number)}
              className={cn(FOOT, card.more > 0 && 'border-t')}
            >
              <span className="flex min-w-0 items-center gap-2">
                <List aria-hidden="true" className="size-4 shrink-0" />
                <span className="truncate">
                  {list.name} · {list.count}
                </span>
              </span>
              <ChevronRight aria-hidden="true" className="size-4 shrink-0" />
            </Link>
          )}
        </div>
      )}
    </li>
  )
})
