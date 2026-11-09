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
  TriangleAlert,
  WifiOff,
} from 'lucide-react'
import { memo, useCallback } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { OUTLINED } from '@/sudarshan/books/cards/parts'
import { istTime } from '@/sudarshan/books/time'
import { keyOf } from '@/sudarshan/dashboard/live'
import { rememberOpenList } from '@/sudarshan/watchlist/openList'
import type { LiveRow } from '@/sudarshan/watchlist/useLiveRows'
import type { LineIcon, LineView } from './cardLine'
import { cardKey, rowsBehind, rowsRun } from './dashboardCache'
import { StatusChip } from './marks'
import { clockFor, ResultLine, rowLiveOf, runWords, sameRowLive } from './Results'
import type { WidgetCard as Card, ScreenStatus } from './types'
import { OptionsButton } from './WidgetMenu'

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
export function Line({ line }: { line: LineView }) {
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
export function ModeChip({ mode }: { mode: Card['mode'] }) {
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

/** Everything in a greyed list takes the stale ink (5.37:1), trend colours included. */
const GREYED = '[&_*]:!text-[color:var(--stale)]'

const FOOT =
  'flex min-h-12 w-full items-center justify-between gap-2 px-4 text-left text-[0.875rem] font-semibold text-primary outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring'

/**
 * One table widget (design 3.6): the screen's name and status, where and when it looks, the card's
 * line, its rows (at least 56 dp, each one button that opens the symbol sheet) with the price and
 * the change ticking for the rows the page follows and the price at the run for the others, and
 * a foot: "and N more" and the screen's automatic list, each 48 dp. The order of the rows changes
 * only at a check: ticks change the figures in place.
 */
export const WidgetCard = memo(WidgetFace, sameCard)

/** What a card's rows draw from the stream, in row order. */
const livesOf = (card: Card, liveByKey: ReadonlyMap<string, LiveRow>) =>
  card.rows_shown.map((row) => rowLiveOf(liveByKey.get(keyOf(row.symbol, row.exchange))))

/**
 * Redraw only when what the card shows changed. The dashboard renders on every tick frame and
 * every clock second; a card none of whose rows moved is skipped, and the clock reaches it only
 * while one of its rows is stale. The callbacks are stable by the dashboard's construction.
 */
function sameCard(a: CardProps, b: CardProps): boolean {
  if (
    a.card !== b.card ||
    a.name !== b.name ||
    a.where !== b.where ||
    a.status !== b.status ||
    a.unreadable !== b.unreadable ||
    a.line.icon !== b.line.icon ||
    a.line.words !== b.line.words ||
    a.line.warn !== b.line.warn ||
    a.line.greyed !== b.line.greyed ||
    a.onOpenScreen !== b.onOpenScreen ||
    a.onOpenRow !== b.onOpenRow ||
    a.onOptions !== b.onOptions ||
    a.onRetry !== b.onRetry
  )
    return false
  const before = livesOf(a.card, a.liveByKey)
  const after = livesOf(b.card, b.liveByKey)
  return after.every(
    (own, at) =>
      sameRowLive(own, before[at] as ReturnType<typeof rowLiveOf>) &&
      clockFor(own, a.now) === clockFor(own, b.now)
  )
}

interface CardProps {
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
  onOptions: (card: Card) => void
}

function WidgetFace({
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
  onOptions,
}: CardProps) {
  const list = card.list
  const behind = rowsBehind(card)
  const open = useCallback((key: string) => onOpenRow(card, key), [onOpenRow, card])
  const own = rowsRun(card)
  const hhmm = (at: number) => istTime(at).slice(0, 5)
  return (
    <li
      data-widget={cardKey(card)}
      aria-label={`${name}, ${where}`}
      className="flex flex-col overflow-hidden rounded-[1rem] border bg-card shadow-[var(--shadow-card)]"
    >
      <div className="flex items-start">
        <button
          type="button"
          onClick={() => onOpenScreen(card.screen_id)}
          aria-label={`${name}: open this screen`}
          className="flex min-h-12 min-w-0 flex-1 flex-col gap-1 pt-3 pl-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
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
        <OptionsButton name={name} onOpen={() => onOptions(card)} />
      </div>
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
      {behind && !card.rows_pending && card.run && own && (
        <div
          data-rows-behind={unreadable ? 'unread' : 'reading'}
          className="flex min-h-12 items-center gap-2 border-t px-4 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]"
        >
          {unreadable ? (
            <>
              <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
              <span className="flex-1">
                These matches are from the {hhmm(own.at)} check; the {hhmm(card.run.at)} check could
                not be read.
              </span>
              <button type="button" onClick={onRetry} className={OUTLINED}>
                <RefreshCw aria-hidden="true" className="size-4" />
                Try again
              </button>
            </>
          ) : (
            <>
              <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin" />
              <span>
                Reading the {hhmm(card.run.at)} matches. These are from the {hhmm(own.at)} check.
              </span>
            </>
          )}
        </div>
      )}
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
          data-greyed={line.greyed || behind || undefined}
          className={cn(
            'divide-y border-t', // Grey by token, never by opacity (which drops ink under 4.5:1): sign, arrow and label stay.
            (line.greyed || behind) && GREYED
          )}
        >
          {card.rows_shown.map((row) => {
            const key = keyOf(row.symbol, row.exchange)
            const lives = rowLiveOf(liveByKey.get(key))
            return (
              <ResultLine
                key={row.key ?? key}
                row={row}
                live={lives}
                runAt={own?.at ?? card.run?.at ?? 0}
                now={clockFor(lives, now)}
                onOpen={open}
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
                  {list.name ?? 'Its list'} · {list.count}
                </span>
              </span>
              <ChevronRight aria-hidden="true" className="size-4 shrink-0" />
            </Link>
          )}
        </div>
      )}
    </li>
  )
}
