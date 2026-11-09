import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Clock, LogOut, Minus, Plus, ScanSearch } from 'lucide-react'
import { Link } from 'react-router'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { cn } from '@/lib/utils'
import { istTime } from '@/sudarshan/books/time'
import { screenerApi } from '@/sudarshan/screener/api'
import { TODAY_KEY } from '@/sudarshan/screener/keys'
import { useHearing, useScreenerPush } from '@/sudarshan/screener/push'
import { useScreenerState } from '@/sudarshan/screener/useScreenerState'
import { keyOf, type MarketOpen } from './live'

/** What stands over the figures when this page is not in the owner's room. */
export const FIGURES_BEHIND = 'Not receiving updates · figures are from the last read'

const SESSION_DAY = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
})
const KEY_DAY = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' })

/**
 * The trading day the figures belong to, from the service's session (YYYYMMDD): 'Session 9 Nov'
 * for today's, 'Last session 6 Nov' for an earlier one, so yesterday's counts never read as
 * today's. Null when the service sent none.
 */
export function sessionWords(session: number | undefined, now: number): string | null {
  if (!session || !/^\d{8}$/.test(String(session))) return null
  const text = String(session)
  const at = Date.UTC(+text.slice(0, 4), +text.slice(4, 6) - 1, +text.slice(6, 8), 6)
  const current = KEY_DAY.format(now).replaceAll('-', '')
  return `${text === current ? 'Session' : 'Last session'} ${SESSION_DAY.format(at)}`
}

const NOT_KNOWN: MarketOpen = () => null
const SHOWN_SCREENS = 4
const SHOWN_RECENT = 3

/**
 * "Screener today" on the dashboard (design 4.7): for each active screen how many instruments
 * match now and who came in and left today, the latest few entries and exits across all of them,
 * and how many alerts have not been looked at. It moves when a run is pushed (the Screener's own
 * listener, joined here too while a screen is running), never on a timer. While the page is not in
 * the owner's room, or the market is closed, it says so and the figures are not passed off as
 * current. Nothing is drawn when the screener is off, has no screens or cannot be read: the
 * Screener page has the detail and its own errors.
 */
export function ScreenerCard({ marketOpen = NOT_KNOWN }: { marketOpen?: MarketOpen }) {
  const client = useQueryClient()
  const { socket } = useSocketContext()
  const state = useScreenerState().data
  const active = state?.enabled ? state.screens.filter((screen) => screen.status === 'active') : []
  useScreenerPush(client, active.length > 0 ? socket : null)
  const hearing = useHearing()
  const today = useQuery({
    queryKey: TODAY_KEY,
    queryFn: () => screenerApi.today(),
    enabled: active.length > 0,
    staleTime: 60_000,
    retry: false,
  }).data

  if (active.length === 0 || !today || !Array.isArray(today.screens)) return null

  const rows = today.screens.slice(0, SHOWN_SCREENS)
  const recent = (Array.isArray(today.recent) ? today.recent : []).slice(0, SHOWN_RECENT)
  const closed = marketOpen('NSE') === false
  const behind = hearing === 'not_joined'
  const unseen = today.unseen_alerts > 0

  return (
    <section
      aria-labelledby="sd-screener-title"
      className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 pt-3 pb-3.5 shadow-[var(--shadow-card)]"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="sd-screener-title" className="flex items-center gap-2 text-[0.9375rem] font-bold">
          <ScanSearch aria-hidden="true" className="size-4" />
          Screener today
          {unseen && (
            <span className="flex h-6 items-center rounded-full bg-[color:var(--gold-tint)] px-2.5 text-xs font-semibold text-[color:var(--warning-text)]">
              {today.unseen_alerts} new {today.unseen_alerts === 1 ? 'alert' : 'alerts'}
            </span>
          )}
        </h2>
        <Link
          to="/screener"
          className="-my-2 -mr-2 inline-flex min-h-12 items-center gap-0.5 rounded-lg px-2 text-[0.84375rem] font-semibold text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          See all
          <ChevronRight aria-hidden="true" className="size-4" />
        </Link>
      </div>

      {sessionWords(today.session, Date.now()) && (
        <p
          data-session={today.session}
          className="text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]"
        >
          {sessionWords(today.session, Date.now())}
        </p>
      )}
      {(behind || closed) && (
        <p
          data-screener-note={behind ? 'behind' : 'closed'}
          className="flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]"
        >
          <Clock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {behind ? FIGURES_BEHIND : 'Market closed · figures are from the last check today'}
        </p>
      )}

      <ul
        className={cn('flex flex-col divide-y', (behind || closed) && 'text-[color:var(--stale)]')}
      >
        {rows.map((screen) => (
          <li key={screen.id}>
            <Link
              to={`/screener?screen=${screen.id}`}
              className="flex min-h-12 items-center justify-between gap-3 py-1.5 outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="min-w-0 truncate text-[0.9375rem] font-semibold">{screen.name}</span>
              <span className="flex shrink-0 items-center gap-2.5 text-[0.8125rem] font-semibold">
                <span className="text-[0.9375rem] font-bold">{screen.matches}</span>
                <span className="inline-flex items-center gap-0.5 text-[color:var(--brand)]">
                  <Plus aria-hidden="true" className="size-3.5" />
                  {screen.entered}
                  <span className="sr-only"> entered</span>
                </span>
                <span className="inline-flex items-center gap-0.5 text-[color:var(--warning-text)]">
                  <Minus aria-hidden="true" className="size-3.5" />
                  {screen.exited}
                  <span className="sr-only"> exited</span>
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>

      {recent.length > 0 && (
        <ul aria-label="Latest changes" className="flex flex-col gap-0.5">
          {recent.map((each) => (
            <li
              key={`${keyOf(each.symbol, each.exchange)}:${each.kind}:${each.at}`}
              className="flex items-center gap-1.5 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]"
            >
              {each.kind === 'entered' ? (
                <Plus aria-hidden="true" className="size-3.5 text-[color:var(--brand)]" />
              ) : (
                <LogOut aria-hidden="true" className="size-3.5 text-[color:var(--warning-text)]" />
              )}
              <span className="font-semibold">{each.symbol}</span>
              {each.kind === 'entered' ? 'entered' : 'left'} {each.screen} ·{' '}
              {istTime(each.at).slice(0, 5)}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
