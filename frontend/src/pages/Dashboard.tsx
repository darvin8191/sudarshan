import {
  AlertTriangle,
  BarChart3,
  BookOpen,
  ChevronRight,
  ExternalLink,
  FileText,
  Search,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { tradingApi } from '@/api/trading'
import { onModeChange } from '@/stores/themeStore'
import { AccessNotice } from '@/sudarshan/AccessNotice'
import { usePriceAccess } from '@/sudarshan/access'
import { useBookSync } from '@/sudarshan/books/useBookSync'
import { AlertsCard } from '@/sudarshan/dashboard/AlertsCard' // SUDARSHAN D5
import { HoldingsCard } from '@/sudarshan/dashboard/HoldingsCard'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { IndexStrip } from '@/sudarshan/dashboard/IndexStrip'
import { Lapsed, type NoPrices } from '@/sudarshan/dashboard/Lapsed' // SUDARSHAN D3
import type { PositionsSnapshot } from '@/sudarshan/dashboard/live'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { type DashboardFunds, PnlCard } from '@/sudarshan/dashboard/PnlCard'
import { PortfolioCard } from '@/sudarshan/dashboard/PortfolioCard'
import { usePortfolioCard } from '@/sudarshan/dashboard/portfolioCard'
import { ScreenerCard } from '@/sudarshan/dashboard/ScreenerCard'
import { StatusRow } from '@/sudarshan/dashboard/StatusRow' // SUDARSHAN D2

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
  // SUDARSHAN: the key once prices can be read (the API key and today's Upstox
  // login, src/sudarshan/access.ts). Until then the index strip and the
  // holdings card stay hidden, and the notice above them says why.
  const access = usePriceAccess()
  const { apiKey } = access
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
  // SUDARSHAN: the market calendar, read again each day (MarketCalendarSource).
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen: marketOpen, anyOpen } = useMarketOpen(market)
  const portfolioCard = usePortfolioCard() // SUDARSHAN M3.11
  const anyMarketOpen = useRef(anyOpen)
  useEffect(() => {
    anyMarketOpen.current = anyOpen
  }, [anyOpen])
  // SUDARSHAN M3.4 (D3, decision 3): no dead end while prices cannot be read. Today's Upstox
  // login has lapsed (the server's session check, or the funds read refused for it) or there
  // is no API key: the status row says so first, one card stands where the indices and the
  // P&L are, and the rest of the page stays. Upstream's broker-expired page is gone (#1400's
  // reconnect is the card's Reconnect).
  const noPrices: NoPrices | null =
    brokerExpired || access.state === 'upstox-logged-out'
      ? 'upstox-logged-out'
      : access.state === 'no-api-key'
        ? 'no-api-key'
        : null

  // Fetch dashboard funds data
  const fetchFundsData = useCallback(async () => {
    // SUDARSHAN: a read that a newer one overtakes is dropped, so an old answer
    // arriving late never replaces a newer one.
    const read = ++readCount.current
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
        setBrokerExpired(false) // SUDARSHAN D3: logged in again
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

  // SUDARSHAN: every reason to read the broker's figures again goes through
  // one place (src/sudarshan/books/useBookSync.ts): each order event, the
  // broker's fills, rejections and square-offs (order_update) included, one
  // read per burst and one more once the broker's books have caught up; a
  // Socket.IO reconnect; coming back after 15 s; and every 45 s while a market
  // is open (or not known to be shut), so the base the ticks move from never
  // drifts far. One read at a time, and none while the page is hidden. Three
  // broker reads each time.
  const marketOpenNow = useCallback(() => anyMarketOpen.current(), [])
  // SUDARSHAN D3: not while the session check says prices cannot be read. A login made elsewhere
  // (auto-login, another phone) brings the key back, and the read above runs again for it; a
  // lapse only the funds read noticed is read again here, so the figures come back by themselves.
  useBookSync(fetchFundsData, { marketOpen: marketOpenNow, enabled: access.state === 'ready' })

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
      <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">Dashboard</h1>
      {/* SUDARSHAN M3.5, M3.6, M3.2 (D2): the market and expiry chips, and the master contract
          only while it is not ready. */}
      <StatusRow day={day} contract={masterContract} noPrices={noPrices} />

      <MarketCalendarSource key={day} onChange={setMarket} />
      {noPrices ? (
        <Lapsed reason={noPrices} />
      ) : (
        <>
          <AccessNotice access={access} />
          <IndexStrip apiKey={apiKey} marketOpen={marketOpen} />
        </>
      )}

      <div className="grid items-start gap-2.5 md:gap-4 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-2.5 md:gap-4">
          {noPrices ? null : error && !marginData ? (
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
              onRetry={fetchFundsData}
              marketOpen={marketOpen}
            />
          )}
          {/* SUDARSHAN M3.11: the Portfolio card, beside the Holdings card while the owner's
              switch (Display settings) is on; it replaces it a release after his check (P5). */}
          {portfolioCard && <PortfolioCard marketOpen={marketOpen} />}
          <HoldingsCard apiKey={apiKey} marketOpen={marketOpen} />
          {/* SUDARSHAN M3.8 (D5): the market alerts fired today, live from the alerts push. */}
          <AlertsCard />
          {/* SUDARSHAN M9 UI8: the screener's day, live from its run pushes. */}
          <ScreenerCard marketOpen={marketOpen} />
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
