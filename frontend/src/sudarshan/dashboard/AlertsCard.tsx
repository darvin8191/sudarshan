import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, ChevronRight, Clock } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Link } from 'react-router'
import { marketAlertsApi } from '@/api/marketAlerts'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { cn } from '@/lib/utils'
import { ALERTS_PATH, firedAt } from '@/sudarshan/alerts/marketAlerts'
import { EVENTS_KEY } from '@/sudarshan/alerts/stateCache'
import { useAlertsHearing, useMarketAlertsState } from '@/sudarshan/alerts/useMarketAlertsState'
import { istDay } from './live'
import { FIGURES_BEHIND } from './ScreenerCard'

/** The card's own page of the latest fires, under the Alerts page's events key, so a reconnect
 * and a settled delivery reach it as they reach the History tab. */
export const RECENT_FIRES_KEY = [...EVENTS_KEY, 'dashboard'] as const
/** How many of today's fires the card lists. */
export const SHOWN_FIRES = 3
/** A burst of fires is read once, this long after its last push. */
const SETTLE_MS = 400

/**
 * "Alerts today" on the dashboard (SUDARSHAN M3.8, D5): the latest three market alerts fired today
 * (what fired, at what time, each opening its alert), how many alerts are active, and the Alerts
 * page. It moves when the alert service pushes a fire (one read per burst) and after a reconnect,
 * never on a timer. While this screen does not hear the owner's room it says so, as the screener
 * card does, and the list is not passed off as current. Nothing is drawn while market alerts are
 * off or cannot be read: the Alerts page has the detail and its own errors.
 */
export function AlertsCard() {
  const client = useQueryClient()
  const state = useMarketAlertsState().data
  const hearing = useAlertsHearing()
  const enabled = state?.enabled === true
  const recent = useQuery({
    queryKey: RECENT_FIRES_KEY,
    queryFn: ({ signal }) => marketAlertsApi.events({ limit: SHOWN_FIRES }, signal),
    enabled,
    staleTime: 30_000,
    retry: false,
  })

  const { socket } = useSocketContext()
  const settle = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (!socket || !enabled) return
    const onFire = () => {
      if (settle.current) clearTimeout(settle.current)
      settle.current = setTimeout(() => {
        settle.current = null
        void client.invalidateQueries({ queryKey: RECENT_FIRES_KEY })
      }, SETTLE_MS)
    }
    socket.on('sudarshan_market_alert', onFire)
    return () => {
      socket.off('sudarshan_market_alert', onFire)
      if (settle.current) clearTimeout(settle.current)
    }
  }, [socket, enabled, client])

  if (!state || !enabled) return null
  const now = Date.now()
  const fires = (recent.data?.events ?? [])
    .filter((event) => !event.test && istDay(event.at) === istDay(now))
    .slice(0, SHOWN_FIRES)
  const active = state.alerts.filter((row) => row.status === 'active').length
  const behind = hearing === 'not_joined'
  // The service's own line when it is not watching ticks now (the feed down, the market shut).
  const engineOk = state.engine.real_time
  const warn = behind || state.engine.code === 'feed_down'

  return (
    <section
      aria-labelledby="sd-alerts-title"
      className="flex flex-col gap-1.5 rounded-[1.125rem] border bg-card px-4 pt-3 pb-3.5 shadow-[var(--shadow-card)]"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="sd-alerts-title" className="flex items-center gap-2 text-[0.9375rem] font-bold">
          <BellRing aria-hidden="true" className="size-4" />
          Alerts today
        </h2>
        <Link
          to={ALERTS_PATH}
          className="-my-2 -mr-2 inline-flex min-h-12 items-center gap-0.5 rounded-lg px-2 text-[0.84375rem] font-semibold text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          See all
          <ChevronRight aria-hidden="true" className="size-4" />
        </Link>
      </div>
      <p className="flex flex-wrap items-center gap-x-1.5 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
        {active === 1 ? '1 active' : `${active} active`}
        {!behind && engineOk && <span className="font-medium">· Hearing alerts</span>}
      </p>
      {(behind || !engineOk) && (
        <p
          data-alerts-note={behind ? 'behind' : state.engine.code}
          className={cn(
            'flex items-start gap-1.5 text-[0.8125rem] font-semibold',
            warn ? 'text-[color:var(--warning-text)]' : 'text-[color:var(--text-tertiary)]'
          )}
        >
          <Clock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {behind ? FIGURES_BEHIND : state.engine.line}
        </p>
      )}
      {fires.length === 0 ? (
        <p className="text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
          {recent.isPending ? 'Reading the latest fires' : 'Nothing has fired today'}
        </p>
      ) : (
        <ul
          aria-label="Latest fires"
          className="flex flex-col divide-y divide-[color:var(--divider)]"
        >
          {fires.map((event) => (
            <li key={event.id}>
              <Link
                to={event.alert_id > 0 ? `${ALERTS_PATH}?alert=${event.alert_id}` : ALERTS_PATH}
                className="flex min-h-12 items-center justify-between gap-3 py-1.5 outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="min-w-0 truncate text-[0.875rem] font-semibold">
                  {event.title}
                </span>
                <span className="shrink-0 text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
                  {firedAt(event.at, now)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
