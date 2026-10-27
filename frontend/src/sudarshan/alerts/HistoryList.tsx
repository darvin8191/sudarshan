import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import {
  BellOff,
  CheckCircle2,
  ChevronDown,
  Clock,
  FlaskConical,
  type LucideIcon,
  PauseCircle,
  Sunrise,
  XCircle,
} from 'lucide-react'
import {
  type AlertEvent,
  type AlertsError,
  type Channel,
  type Delivery,
  type EventsPage,
  marketAlertsApi,
} from '@/api/marketAlerts'
import { cn } from '@/lib/utils'
import { EmptyCard, LoadingCards, OUTLINED, ProblemCard } from '@/sudarshan/books/cards/parts'
import { istTime } from '@/sudarshan/books/time'
import { describe } from '@/sudarshan/watchlist/rows'
import { firedAt, withRecentDeliveries } from './marketAlerts'
import { firesKey, HISTORY_KEY } from './stateCache'

/**
 * What fired (M10.4): each event with its time in India, the server's words and value as they
 * were frozen when it fired, its marks, and how each channel delivered it. Newest first, a page
 * at a time; a page read while a delivery settled takes that delivery (withRecentDeliveries),
 * and later ones arrive by push (marketAlerts.ts), never by reading again on a timer.
 */

const PAGE = 50

export const CHANNELS: Array<[Channel, string]> = [
  ['telegram', 'Telegram'],
  ['app', 'App'],
]

const DELIVERY: Record<Delivery['status'], { word: string; icon: LucideIcon; className: string }> =
  {
    sent: {
      word: 'Sent',
      icon: CheckCircle2,
      className: 'bg-[color:var(--up-tint)] text-[color:var(--up)]',
    },
    pending: {
      word: 'Sending',
      icon: Clock,
      className: 'bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]',
    },
    not_sent: {
      word: 'Not sent',
      icon: XCircle,
      className: 'bg-[color:var(--down-tint)] text-[color:var(--down)]',
    },
    off: {
      word: 'Off',
      icon: BellOff,
      className: 'bg-[color:var(--surface-2)] text-[color:var(--neutral)]',
    },
  }

const CHIP =
  'inline-flex h-6 shrink-0 items-center gap-1 rounded-md px-2 text-[0.71875rem] leading-none font-bold tracking-wide'

/** One channel's delivery: its word and icon on a tint, its time once sent, and why below. */
export function DeliveryChip({ name, delivery }: { name: string; delivery: Delivery }) {
  const look = DELIVERY[delivery.status] ?? DELIVERY.pending
  const Icon = look.icon
  const when = delivery.status === 'sent' ? ` ${istTime(delivery.updated_at)}` : ''
  return (
    <span className="flex flex-col items-start gap-0.5">
      <span data-delivery={delivery.status} className={cn(CHIP, look.className)}>
        <Icon aria-hidden="true" className="size-3.5" />
        {`${name} · ${look.word}${when}`}
      </span>
      {delivery.reason && (
        <span className="text-xs font-medium text-[color:var(--text-secondary)]">
          {delivery.reason}
        </span>
      )}
    </span>
  )
}

/** A formatted price back to a number: "₹1,23,456.80" is 123456.8. */
function figure(text: string | null): number | null {
  const n = Number(String(text ?? '').replace(/[^\d.]/g, ''))
  return text && Number.isFinite(n) ? n : null
}

/** The marks an event carries: found after a pause, a gap at the open, a test. */
function marksOf(event: AlertEvent): Array<{ word: string; icon: LucideIcon }> {
  const marks: Array<{ word: string; icon: LucideIcon }> = []
  if (event.test) marks.push({ word: 'Test', icon: FlaskConical })
  if (event.found_later) marks.push({ word: 'Seen after a pause', icon: PauseCircle })
  if (event.opening) {
    const value = figure(event.value)
    const level = figure(event.level)
    const way =
      value === null || level === null || value === level ? '' : value > level ? ' above' : ' below'
    marks.push({ word: `Opened${way}`, icon: Sunrise })
  }
  return marks
}

/** One fired alert, as History and an alert's sheet show it. */
export function EventItem({ event, now }: { event: AlertEvent; now: number }) {
  const named = event.symbol ? describe(event.symbol, event.exchange) : null
  const detail = [event.detail, event.note ? `Note: ${event.note}` : ''].filter(Boolean).join(' · ')
  return (
    <article
      data-event={event.id}
      className="flex flex-col gap-1.5 rounded-[1rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-xs font-semibold text-[color:var(--text-tertiary)]">
          {named ? `${named.title} · ${named.detail}` : ''}
        </span>
        <time
          dateTime={new Date(event.at).toISOString()}
          className="shrink-0 text-xs font-semibold text-[color:var(--text-secondary)]"
        >
          {firedAt(event.at, now)}
        </time>
      </div>
      <p className="text-sm font-bold">{event.title}</p>
      {detail && <p className="text-sm font-medium text-[color:var(--text-secondary)]">{detail}</p>}
      {marksOf(event).length > 0 && (
        <p className="flex flex-wrap gap-1.5">
          {marksOf(event).map(({ word, icon: Icon }) => (
            <span
              key={word}
              data-mark={word}
              className={cn(CHIP, 'bg-[color:var(--brand-tint)] text-[color:var(--brand)]')}
            >
              <Icon aria-hidden="true" className="size-3.5" />
              {word}
            </span>
          ))}
        </p>
      )}
      <div className="flex flex-wrap gap-x-3 gap-y-1.5">
        {CHANNELS.map(([channel, name]) => {
          const delivery = event.deliveries?.[channel]
          return delivery ? <DeliveryChip key={channel} name={name} delivery={delivery} /> : null
        })}
      </div>
    </article>
  )
}

/** The History tab: every fire and test, newest first, older pages on request. */
export function HistoryList({ now }: { now: number }) {
  const history = useInfiniteQuery<EventsPage, AlertsError>({
    queryKey: HISTORY_KEY,
    queryFn: ({ pageParam }) =>
      marketAlertsApi
        .events({ before: pageParam as number | null, limit: PAGE })
        .then(withRecentDeliveries),
    initialPageParam: null,
    getNextPageParam: (last) => (last.more ? (last.events.at(-1)?.id ?? null) : null),
    staleTime: 30_000,
    retry: false,
  })
  if (history.isPending) return <LoadingCards />
  if (history.isError && !history.data) {
    return <ProblemCard message={history.error.message} onRetry={() => void history.refetch()} />
  }
  const events = history.data?.pages.flatMap((page) => page.events) ?? []
  if (events.length === 0) {
    return (
      <EmptyCard
        title="Nothing has fired yet"
        hint="Each alert that fires shows here, with where it was sent, for 100 days."
      />
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <ol className="flex flex-col gap-2">
        {events.map((event) => (
          <li key={event.id}>
            <EventItem event={event} now={now} />
          </li>
        ))}
      </ol>
      {history.isFetchNextPageError && (
        <p role="alert" className="px-1 text-sm font-semibold text-[color:var(--down)]">
          {history.error?.message}
        </p>
      )}
      {history.hasNextPage && (
        <button
          type="button"
          disabled={history.isFetchingNextPage}
          onClick={() => void history.fetchNextPage()}
          className={cn(OUTLINED, 'self-center')}
        >
          <ChevronDown aria-hidden="true" className="size-5" />
          {history.isFetchingNextPage ? 'Reading older alerts' : 'Show older'}
        </button>
      )}
    </div>
  )
}

/** An alert's last five fires, in its sheet. */
export function RecentFires({ alertId, now }: { alertId: number; now: number }) {
  const fires = useQuery<EventsPage, AlertsError>({
    queryKey: firesKey(alertId),
    queryFn: () => marketAlertsApi.events({ alert: alertId, limit: 5 }).then(withRecentDeliveries),
    staleTime: 30_000,
    retry: false,
  })
  const events = fires.data?.events ?? []
  return (
    <section aria-label="Last fires" className="flex flex-col gap-1.5">
      <h3 className="text-sm font-bold">Last fires</h3>
      {fires.isError ? (
        <p className="text-sm font-medium text-[color:var(--text-secondary)]">
          {fires.error.message}
        </p>
      ) : fires.isPending ? (
        <p className="text-sm font-medium text-muted-foreground">Reading its fires</p>
      ) : events.length === 0 ? (
        <p className="text-sm font-medium text-muted-foreground">Not fired yet.</p>
      ) : (
        <ol className="flex flex-col gap-2">
          {events.map((event) => (
            <li key={event.id}>
              <EventItem event={event} now={now} />
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
