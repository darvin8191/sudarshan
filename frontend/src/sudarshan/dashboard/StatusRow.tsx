import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CalendarClock, Clock, Hourglass, Moon, RotateCw } from 'lucide-react'
import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { useOnReturn } from '@/sudarshan/books/useReturn'
import { HoursSheet } from './HoursSheet'
import {
  type Chip,
  type DayFacts,
  expiryChip,
  marketChip,
  nextEdge,
  readDayFacts,
  type Tone,
} from './marketPhase'

export const DAY_FACTS_KEY = ['sudarshan', 'session', 'today'] as const
const READ_TIMEOUT_MS = 8_000

async function loadDayFacts(signal: AbortSignal): Promise<DayFacts> {
  const abort = new AbortController()
  const stop = () => abort.abort()
  signal.addEventListener('abort', stop)
  const timer = setTimeout(stop, READ_TIMEOUT_MS)
  try {
    const res = await fetch('/sudarshan/session/today', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal: abort.signal,
    })
    const body = await res.json().catch(() => null)
    const facts = res.ok && body?.status === 'success' ? readDayFacts(body.data) : null
    if (!facts) throw new Error('day facts not read')
    return facts
  } finally {
    clearTimeout(timer)
    signal.removeEventListener('abort', stop)
  }
}

/**
 * The day's facts: read when the page opens, again for a new day in India (`day` is the key) and
 * when the page comes back after 15 s away (the calendar may have been edited meanwhile). Not
 * market data, and no timer reads it.
 */
export function useDayFacts(day: string) {
  const query = useQuery({
    queryKey: [...DAY_FACTS_KEY, day],
    queryFn: ({ signal }) => loadDayFacts(signal),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const { refetch } = query
  const retry = useCallback(() => void refetch(), [refetch])
  useOnReturn(retry)
  return { facts: query.data ?? null, failed: query.isError, retry }
}

/** Now, moved at the next moment a chip changes and when the page comes back into view: one timeout, never a repeating one. */
function useEdgeClock(edge: number | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const wake = () => {
      if (document.visibilityState === 'visible') setNow(Date.now())
    }
    document.addEventListener('visibilitychange', wake)
    const wait = edge === null ? null : Math.min(Math.max(edge - Date.now(), 0) + 50, 3_600_000)
    const timer = wait === null ? undefined : setTimeout(() => setNow(Date.now()), wait)
    return () => {
      document.removeEventListener('visibilitychange', wake)
      clearTimeout(timer)
    }
  }, [edge])
  return now
}

const TONE: Record<Tone | 'down', string> = {
  neutral: 'border-border bg-card text-[color:var(--text-secondary)]',
  gold: 'border-[color:var(--gold)] bg-[color:var(--gold-tint)] text-[color:var(--gold-ink)]',
  live: 'border-[color:var(--live-border)] bg-[color:var(--live-tint)] text-[color:var(--live-ink)]',
  down: 'border-[color:var(--down)] bg-[color:var(--down-tint)] text-[color:var(--down)]',
}
const ICON = 'size-4 shrink-0'
/** A 48dp target around a 32px pill, the row kept tight by the negative margin. */
const TARGET =
  '-my-1.5 inline-flex min-h-12 max-w-full items-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring'

function Pill({
  tone,
  icon,
  children,
}: {
  tone: Tone | 'down'
  icon: ReactNode
  children: ReactNode
}) {
  return (
    <span
      className={cn(
        'flex min-h-8 min-w-0 items-center gap-1.5 rounded-full border px-3 py-1 text-[0.8125rem] leading-tight font-semibold',
        TONE[tone]
      )}
    >
      {icon}
      <span className="min-w-0">{children}</span>
    </span>
  )
}

function chipIcon(chip: Chip): ReactNode {
  if (chip.icon === 'dot')
    return <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-current" />
  const Icon = { clock: Clock, moon: Moon, alert: AlertTriangle, expiry: CalendarClock }[chip.icon]
  return <Icon aria-hidden="true" className={ICON} />
}

export interface MasterContractState {
  status: 'pending' | 'downloading' | 'success' | 'error'
  message?: string
}

/**
 * The dashboard's status row (design 3.3): what needs attention first (a master contract not
 * ready), then the market chip and, on an expiry day or after the close before one, the expiry
 * chip. Each chip is its own statement in words with an icon, never colour alone; a tap on the
 * market or expiry chip opens the day's hours for every exchange.
 */
export function StatusRow({ day, contract }: { day: string; contract: MasterContractState }) {
  const { facts, failed, retry } = useDayFacts(day)
  const now = useEdgeClock(nextEdge(facts, Date.now()))
  const [open, setOpen] = useState(false)
  const market = marketChip(facts, failed, now)
  const expiry = expiryChip(facts, now)
  const showHours = () => setOpen(true)

  return (
    <div
      data-testid="status-row"
      data-ready={facts ? 'true' : 'false'}
      className="flex flex-wrap items-center gap-x-2 gap-y-2"
    >
      {(contract.status === 'downloading' || contract.status === 'error') && (
        <Link to="/master-contract" className={TARGET} title={contract.message}>
          <Pill
            tone={contract.status === 'error' ? 'down' : 'gold'}
            icon={
              contract.status === 'error' ? (
                <AlertTriangle aria-hidden="true" className={ICON} />
              ) : (
                <Hourglass aria-hidden="true" className={ICON} />
              )
            }
          >
            {contract.status === 'error' ? 'Master contract failed' : 'Downloading master contract'}
          </Pill>
        </Link>
      )}
      {facts ? (
        <button type="button" onClick={showHours} className={TARGET} data-market={market.kind}>
          <Pill tone={market.tone} icon={chipIcon(market)}>
            {market.words}
            <span className="sr-only">. Market hours for every exchange</span>
          </Pill>
        </button>
      ) : (
        <output className={TARGET} data-market={market.kind}>
          <Pill tone={market.tone} icon={chipIcon(market)}>
            {market.words}
          </Pill>
        </output>
      )}
      {failed && !facts && (
        <button
          type="button"
          onClick={retry}
          className="-my-1.5 inline-flex min-h-12 items-center gap-1.5 rounded-lg px-2 text-[0.8125rem] font-semibold text-[color:var(--brand)] outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RotateCw aria-hidden="true" className={ICON} />
          Try again<span className="sr-only"> to read the market hours</span>
        </button>
      )}
      {expiry && (
        <button type="button" onClick={showHours} className={TARGET} data-expiry={expiry.kind}>
          <Pill tone={expiry.tone} icon={chipIcon(expiry)}>
            {expiry.words}
          </Pill>
        </button>
      )}
      {facts && <HoursSheet facts={facts} now={now} open={open} onOpenChange={setOpen} />}
    </div>
  )
}
