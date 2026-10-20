import {
  AlertTriangle,
  BarChart3,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  FileText,
  Loader2,
  PlugZap,
  Search,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { tradingApi } from '@/api/trading'
import { useMarketStatus } from '@/hooks/useMarketStatus'
import { useOrderEventRefresh } from '@/hooks/useOrderEventRefresh'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { onModeChange } from '@/stores/themeStore'
import { HoldingsCard } from '@/sudarshan/dashboard/HoldingsCard'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { IndexStrip } from '@/sudarshan/dashboard/IndexStrip'
import { type PositionsSnapshot, RESYNC_MS } from '@/sudarshan/dashboard/live'
import { type DashboardFunds, PnlCard } from '@/sudarshan/dashboard/PnlCard'
import { formatAmount } from '@/sudarshan/dashboard/format'

interface MasterContractStatus {
  status: 'pending' | 'downloading' | 'success' | 'error'
  message?: string
  total_symbols?: number
}

// SUDARSHAN: the D home, live. The funds call and its error handling are
// upstream's. Added: the position book, read with the funds so the P&L can move
// tick by tick between reads (src/sudarshan/dashboard/live.ts); a re-read every
// 45 seconds while a market is open, on top of upstream's order-event refresh;
// and the index strip and holdings summary. Existing APIs only.

/**
 * The position book, stamped with when it was asked for, so that only ticks
 * newer than it move the P&L. Null when there is no API key to ask with or the
 * read fails: the P&L then holds the broker's figures until the next read.
 */
async function readPositions(apiKey: string | null): Promise<PositionsSnapshot | null> {
  if (!apiKey) return null
  const at = Date.now()
  try {
    const response = await tradingApi.getPositions(apiKey)
    return response.status === 'success' && Array.isArray(response.data)
      ? { positions: response.data, at }
      : null
  } catch {
    return null
  }
}

/** The master contract's state as a chip: an icon and words, never a coloured dot alone. */
const CONTRACT_CHIP = {
  success: {
    icon: CheckCircle2,
    label: 'Master contract ready',
    tone: 'border-[color:var(--live-border)] bg-[color:var(--live-tint)] text-[color:var(--live-ink)]',
  },
  downloading: {
    icon: Loader2,
    label: 'Downloading master contract',
    tone: 'border-[color:var(--gold)] bg-[color:var(--gold-tint)] text-[color:var(--gold-ink)]',
  },
  error: {
    icon: AlertTriangle,
    label: 'Master contract failed',
    tone: 'border-[color:var(--down)] bg-[color:var(--down-tint)] text-[color:var(--down)]',
  },
  pending: {
    icon: Loader2,
    label: 'Checking master contract',
    tone: 'border-border bg-muted text-muted-foreground',
  },
} as const

const QUICK_ACCESS = [
  {
    href: '/search',
    label: 'Symbols',
    description: 'Universal symbology across brokers',
    icon: Search,
  },
  {
    href: '/pnl-tracker',
    label: 'P&L Tracker',
    description: 'Live intraday MTM tracker',
    icon: BarChart3,
  },
  {
    href: '/logs',
    label: 'Live Logs',
    description: 'Real-time trading activity logs',
    icon: FileText,
  },
  {
    href: '/logs/latency',
    label: 'Latency Monitor',
    description: 'Monitor order & API latency',
    icon: Zap,
  },
  {
    href: 'https://docs.openalgo.in',
    label: 'Documentation',
    description: 'Tutorials, API docs & features',
    icon: BookOpen,
    external: true,
  },
] as const

export default function Dashboard() {
  const [marginData, setMarginData] = useState<DashboardFunds | null>(null)
  const apiKey = useAuthStore((s) => s.apiKey)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [masterContract, setMasterContract] = useState<MasterContractStatus>({
    status: 'pending',
  })
  const [isAuthenticated, setIsAuthenticated] = useState(true) // Assume authenticated initially
  // Broker token revoked/expired while the app session is still valid
  // (daily token rollover). Routes the user to /broker, not /login (#1400).
  const [brokerExpired, setBrokerExpired] = useState(false)
  // SUDARSHAN: the position book read with the funds, and when that was.
  const [positions, setPositions] = useState<PositionsSnapshot | null>(null)
  const [syncedAt, setSyncedAt] = useState<number | null>(null)
  const readCount = useRef(0)
  const lastReadAt = useRef(0)
  const market = useMarketStatus()
  const { isOpen: marketOpen, anyOpen } = useMarketOpen(market)
  const anyMarketOpen = useRef(anyOpen)
  useEffect(() => {
    anyMarketOpen.current = anyOpen
  }, [anyOpen])

  // Fetch dashboard funds data
  const fetchFundsData = useCallback(async () => {
    // SUDARSHAN: a read that a newer one overtakes is dropped, so an old answer
    // arriving late never replaces a newer one.
    const read = ++readCount.current
    lastReadAt.current = Date.now()
    try {
      setIsLoading(true)
      // SUDARSHAN: the position book first, then the funds. Analyze mode marks
      // its positions to market when the book is read and its funds carry that
      // figure, so in this order the funds and the book agree on prices.
      const book = await readPositions(apiKey)
      const response = await fetch('/auth/dashboard-data', {
        credentials: 'include',
      })
      if (read !== readCount.current) return

      if (response.status === 401) {
        const body = await response.json().catch(() => null)
        if (body?.code === 'BROKER_SESSION_EXPIRED') {
          setBrokerExpired(true)
        } else {
          setIsAuthenticated(false)
        }
        setIsLoading(false)
        return
      }

      const data = await response.json()
      if (read !== readCount.current) return

      if (data.status === 'success' && data.data) {
        setMarginData(data.data)
        setPositions(book)
        setSyncedAt(Date.now())
        setError(null)
      } else {
        setError(data.message || 'Failed to fetch margin data')
      }
    } catch (_err) {
      if (read === readCount.current) setError('Failed to fetch margin data')
    } finally {
      if (read === readCount.current) setIsLoading(false)
    }
  }, [apiKey])

  useEffect(() => {
    fetchFundsData()
  }, [fetchFundsData])

  // Refresh funds when an order is placed (via SocketIO event)
  useOrderEventRefresh(fetchFundsData, {
    events: ['order_event', 'analyzer_update', 'close_position_event'],
  })

  // SUDARSHAN: re-read the broker's figures every 45 seconds while the page is
  // in view and a market is open (or not known to be shut), so the base the
  // ticks move from never drifts far; and on coming back into view after more
  // than 15 seconds away. Three broker reads each time, well inside its limits.
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible' && anyMarketOpen.current() !== false) {
        fetchFundsData()
      }
    }, RESYNC_MS)
    const shown = () => {
      if (
        document.visibilityState === 'visible' &&
        Date.now() - lastReadAt.current > RESYNC_MS / 3
      ) {
        fetchFundsData()
      }
    }
    document.addEventListener('visibilitychange', shown)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', shown)
    }
  }, [fetchFundsData])

  // Listen for mode changes and refresh data
  useEffect(() => {
    const unsubscribe = onModeChange(() => {
      // Refresh funds data when mode changes
      fetchFundsData()
    })
    return () => unsubscribe()
  }, [fetchFundsData])

  // Check master contract status
  const checkMasterContractStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/master-contract/status', {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      })

      if (response.status === 401) {
        return
      }

      const data = await response.json()
      setMasterContract(data)
    } catch (_err) {
      setMasterContract({ status: 'error', message: 'Failed to check status' })
    }
  }, [])

  useEffect(() => {
    checkMasterContractStatus()

    // Poll every 5 seconds until successful
    const interval = setInterval(() => {
      setMasterContract((prev) => {
        if (prev.status === 'success') {
          return prev // Don't check again if already successful
        }
        checkMasterContractStatus()
        return prev
      })
    }, 5000)

    return () => clearInterval(interval)
  }, [checkMasterContractStatus])

  const contract = CONTRACT_CHIP[masterContract.status] ?? CONTRACT_CHIP.pending
  const ContractIcon = contract.icon
  const spinning = masterContract.status === 'downloading' || masterContract.status === 'pending'

  // Broker token expired but the app session is fine: send the user to the
  // broker reconnect flow, not /login (which would bounce back) — #1400.
  if (brokerExpired) {
    return (
      <div className="mx-auto flex min-h-[50vh] max-w-md flex-col items-center justify-center gap-4 text-center">
        <span className="flex size-14 items-center justify-center rounded-2xl bg-[color:var(--gold-tint)] text-[color:var(--gold-ink)]">
          <PlugZap aria-hidden="true" className="size-7" />
        </span>
        <h1 className="sd-display text-[1.75rem] leading-tight font-bold">
          Broker Session Expired
        </h1>
        <p className="text-muted-foreground">
          Your broker token has expired (brokers roll tokens daily). Reconnect to continue trading.
        </p>
        <Link
          to="/broker"
          className="inline-flex min-h-12 items-center justify-center rounded-[0.875rem] bg-primary px-6 text-base font-semibold text-primary-foreground outline-none hover:bg-primary/90 focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          Reconnect Broker
        </Link>
      </div>
    )
  }

  // If not authenticated, show login prompt
  if (!isAuthenticated) {
    return (
      <div className="mx-auto flex min-h-[50vh] max-w-md flex-col items-center justify-center gap-4 text-center">
        <h1 className="sd-display text-[1.75rem] leading-tight font-bold">Session Expired</h1>
        <p className="text-muted-foreground">Please log in to access the dashboard.</p>
        <Link
          to="/login"
          className="inline-flex min-h-12 items-center justify-center rounded-[0.875rem] bg-primary px-6 text-base font-semibold text-primary-foreground outline-none hover:bg-primary/90 focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          Go to Login
        </Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2.5 md:gap-4">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">
          Dashboard
        </h1>
        <Link
          to="/master-contract"
          className="-my-1.5 inline-flex min-h-12 items-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring"
          title={masterContract.message}
        >
          <span
            className={cn(
              'flex h-8 items-center gap-1.5 rounded-full border px-3 text-[0.8125rem] font-semibold',
              contract.tone
            )}
          >
            <ContractIcon
              aria-hidden="true"
              className={cn('size-4 shrink-0', spinning && 'animate-spin')}
            />
            {contract.label}
            {masterContract.status === 'success' && masterContract.total_symbols ? (
              <span className="hidden font-medium sm:inline">
                · {formatAmount(masterContract.total_symbols, 0)} symbols
              </span>
            ) : null}
          </span>
        </Link>
      </div>

      <IndexStrip apiKey={apiKey} marketOpen={marketOpen} />

      <div className="grid items-start gap-2.5 md:gap-4 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-2.5 md:gap-4">
          {error && !marginData ? (
            <section
              role="alert"
              className="flex flex-col items-start gap-3 rounded-[1.125rem] border border-[color:var(--down)] bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
            >
              <div className="flex items-start gap-2.5">
                <AlertTriangle
                  aria-hidden="true"
                  className="mt-0.5 size-5 shrink-0 text-[color:var(--down)]"
                />
                <div className="flex flex-col gap-0.5">
                  <h2 className="text-[0.9375rem] font-bold">
                    Funds and P&amp;L could not be loaded
                  </h2>
                  <p className="text-sm text-muted-foreground">{error}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={fetchFundsData}
                className="inline-flex min-h-12 items-center rounded-[0.875rem] border border-input bg-card px-4 text-sm font-semibold outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                Try again
              </button>
            </section>
          ) : (
            <PnlCard
              funds={marginData}
              loading={isLoading && !marginData}
              positions={positions}
              syncedAt={syncedAt}
              syncFailed={error !== null}
              marketOpen={marketOpen}
            />
          )}
          <HoldingsCard apiKey={apiKey} />
        </div>

        <section
          aria-labelledby="sd-quick-access"
          className="min-w-0 rounded-[1.125rem] border bg-card px-4 pt-1 pb-1 shadow-[var(--shadow-card)]"
        >
          <h2 id="sd-quick-access" className="flex h-11 items-center text-[0.9375rem] font-bold">
            Quick Access
          </h2>
          <ul>
            {QUICK_ACCESS.map((item) => {
              const Icon = item.icon
              const inner = (
                <>
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-[0.6875rem] bg-[color:var(--brand-tint)] text-[color:var(--brand)]">
                    <Icon aria-hidden="true" className="size-[1.125rem]" />
                  </span>
                  <span className="flex min-w-0 flex-1 flex-col gap-px">
                    <span className="text-[0.90625rem] font-semibold">{item.label}</span>
                    <span className="text-[0.78125rem] leading-snug font-medium text-[color:var(--text-tertiary)]">
                      {item.description}
                    </span>
                  </span>
                  {'external' in item ? (
                    <ExternalLink
                      aria-hidden="true"
                      className="size-4 shrink-0 text-[color:var(--text-tertiary)]"
                    />
                  ) : (
                    <ChevronRight
                      aria-hidden="true"
                      className="size-[1.125rem] shrink-0 text-[color:var(--text-tertiary)]"
                    />
                  )}
                </>
              )
              const row =
                'flex min-h-14 items-center gap-3 rounded-lg py-2 outline-none hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring'
              return (
                <li key={item.href} className="border-t border-[color:var(--divider)]">
                  {'external' in item ? (
                    <a href={item.href} target="_blank" rel="noopener noreferrer" className={row}>
                      {inner}
                      <span className="sr-only">(opens in a new tab)</span>
                    </a>
                  ) : (
                    <Link to={item.href} className={row}>
                      {inner}
                    </Link>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      </div>
    </div>
  )
}
