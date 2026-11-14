import { useQuery } from '@tanstack/react-query'
import { KeyRound, Unplug } from 'lucide-react'
import { Link } from 'react-router'
import { autologinApi } from '@/api/autologin'
import { istDay } from './live'
import { hhmm, whenWords } from './marketPhase'

/** Why prices cannot be read now: today's Upstox login has lapsed, or there is no API key. */
export type NoPrices = 'upstox-logged-out' | 'no-api-key'

const ACTION =
  'inline-flex min-h-12 items-center justify-center rounded-[0.875rem] bg-primary px-6 text-base font-semibold text-primary-foreground outline-none hover:bg-primary/90 focus-visible:ring-[3px] focus-visible:ring-ring/50'
const QUIET_LINK =
  'inline-flex min-h-12 items-center rounded-sm text-sm font-semibold text-[color:var(--brand)] underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring'

/**
 * When auto-login will log in again, where it is on (its own page's status, read once, never on
 * a timer). Nothing when it is off or has no next run.
 */
function AutoLoginLine() {
  // The Auto login page's own key, so a status it read is shown at once.
  const { data } = useQuery({
    queryKey: ['sudarshan-autologin-status'],
    queryFn: autologinApi.status,
    staleTime: 60_000,
    retry: false,
  })
  if (!data?.enabled) return null
  const next = data.next_run ? Date.parse(data.next_run) : Number.NaN
  let words = 'Auto login is logging in now'
  if (!data.running) {
    if (!Number.isFinite(next)) return null
    const now = Date.now()
    words = `Auto login runs ${istDay(next) === istDay(now) ? `at ${hhmm(next)}` : whenWords(next, now)}`
  }
  return (
    <Link to="/autologin" className={QUIET_LINK}>
      {words}
    </Link>
  )
}

/**
 * SUDARSHAN M3.4 (D3): what stands where the index strip and the P&L card are while prices cannot
 * be read (decision 3: no dead end). The rest of the dashboard stays with its own lines: the
 * Portfolio card reads Upstox through the service, and the alerts and the screener through theirs.
 */
export function Lapsed({ reason }: { reason: NoPrices }) {
  const loggedOut = reason === 'upstox-logged-out'
  const Icon = loggedOut ? Unplug : KeyRound
  return (
    <section
      aria-labelledby="sd-no-prices"
      data-no-prices={reason}
      className="flex flex-col items-start gap-2 rounded-[1.125rem] border border-[color:var(--gold)] bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
    >
      <h2 id="sd-no-prices" className="flex items-start gap-2.5 text-[0.9375rem] font-bold">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-[0.625rem] bg-[color:var(--gold-tint)] text-[color:var(--gold-ink)]">
          <Icon aria-hidden="true" className="size-[1.125rem]" />
        </span>
        <span className="pt-1">
          {loggedOut ? "Prices and orders need today's Upstox login" : 'Prices need an API key'}
        </span>
      </h2>
      <p className="text-sm text-muted-foreground">
        {loggedOut
          ? 'Upstox ends every login at 03:30. Your funds, P&L and the indices come back here as soon as you log in.'
          : 'Make a key once; the funds, P&L and the indices then show here by themselves.'}
      </p>
      {loggedOut ? (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
          <Link to="/broker" className={ACTION}>
            Reconnect
          </Link>
          <AutoLoginLine />
        </div>
      ) : (
        <Link to="/apikey" className={ACTION}>
          Create an API key
        </Link>
      )}
    </section>
  )
}
