import {
  AlertTriangle,
  ArrowUpDown,
  ChartCandlestick,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  RefreshCw,
  Settings2,
  TrendingDown,
  TrendingUp,
  X,
} from 'lucide-react'
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useLivePrice } from '@/hooks/useLivePrice'
import { useSupportedExchanges } from '@/hooks/useSupportedExchanges'
import { cn, makeFormatCurrency, sanitizeCSV } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { onModeChange } from '@/stores/themeStore'
// SUDARSHAN M6.9: on a phone the positions are cards (src/sudarshan/books/cards).
import { units } from '@/sudarshan/books/cards/format'
import { PositionCards, usePositionExits } from '@/sudarshan/books/cards/PositionCards'
import { WarningLine } from '@/sudarshan/books/cards/parts'
// SUDARSHAN: the book re-read, status line and tick prices (src/sudarshan/books).
import { useBookFeed } from '@/sudarshan/books/feed'
import { TickPrice } from '@/sudarshan/books/LiveFigures'
// SUDARSHAN M6.9: what became of an exit: sent, refused, or not confirmed (it may have gone).
import {
  answerBody,
  answeredHeld,
  EXIT_ALL_TOAST,
  exitAllEach,
  heldText,
  refusalReason,
  sentOrderIds,
  stillPlacing,
  thrownOutcome,
} from '@/sudarshan/books/sendOutcome'
import type { SendOutcome, SentAnswer } from '@/sudarshan/books/sent'
import {
  BUY_INK,
  DANGER_OUTLINE,
  FILTER_DOT,
  pnlTone,
  QUIET_CHIP,
  SELL_INK,
  signedMoney,
  WARNING_BOX,
  WARNING_INK,
} from '@/sudarshan/books/tableColours' // SUDARSHAN B8 (M6.6)
import { useBookSync, useLatestRead } from '@/sudarshan/books/useBookSync'
// SUDARSHAN M4.15: on a phone, a symbol opens the phone chart (src/sudarshan/chart).
import { BookSymbolLink } from '@/sudarshan/chart/BookSymbolLink'
import { usePhoneLayout } from '@/sudarshan/chart/usePhoneLayout'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { spokenContract } from '@/sudarshan/watchlist/rows'
import type { Position } from '@/types/trading'
import { showToast } from '@/utils/toast'

const STORAGE_KEY = 'openalgo_positions_prefs'

type GroupingType = 'none' | 'underlying' | 'underlying_expiry'
type SortColumn = 0 | 3 | 4 | 6 | 7 | null
type SortDirection = 'asc' | 'desc'

interface FilterState {
  product: string[]
  direction: string[]
  exchange: string[]
}

interface Preferences {
  grouping: GroupingType
  filters: FilterState
}

function parseSymbol(symbol: string, exchange: string) {
  if (exchange === 'NSE' || exchange === 'BSE') {
    return { underlying: symbol, expiry: null, strike: null, optionType: null }
  }

  const futMatch = symbol.match(/^(.+?)(\d{1,2}[A-Z]{3}\d{2})FUT$/i)
  if (futMatch) {
    return { underlying: futMatch[1], expiry: futMatch[2], strike: null, optionType: 'FUT' }
  }

  const optMatch = symbol.match(/^(.+?)(\d{1,2}[A-Z]{3}\d{2})(\d+\.?\d*)(CE|PE)$/i)
  if (optMatch) {
    return {
      underlying: optMatch[1],
      expiry: optMatch[2],
      strike: optMatch[3],
      optionType: optMatch[4],
    }
  }

  return { underlying: symbol, expiry: null, strike: null, optionType: null }
}

function calculatePnlPercent(position: Position): number {
  const avgPrice = Number(position.average_price) || 0
  const qty = Number(position.quantity) || 0
  const pnl = Number(position.pnl) || 0

  // Use API-provided pnlpercent if available
  if (position.pnlpercent !== undefined && position.pnlpercent !== null) {
    return Number(position.pnlpercent) || 0
  }

  if (avgPrice === 0) return 0

  // For open positions with quantity, calculate based on investment
  if (qty !== 0) {
    const investment = Math.abs(avgPrice * qty)
    return investment > 0 ? (pnl / investment) * 100 : 0
  }

  // For closed positions (qty=0), return 0% like Zerodha
  // We cannot reliably calculate P&L% without knowing the original quantity
  // The P&L amount is still shown correctly from the API
  return 0
}

export default function Positions() {
  const { apiKey, user } = useAuthStore()
  const { isCrypto } = useSupportedExchanges()
  const formatCurrency = useMemo(() => makeFormatCurrency(user?.broker), [user?.broker])
  const [positions, setPositions] = useState<Position[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showStaleWarning, setShowStaleWarning] = useState(false)

  // Filter and grouping state
  const [grouping, setGrouping] = useState<GroupingType>('none')
  const [filters, setFilters] = useState<FilterState>({
    product: [],
    direction: [],
    exchange: [],
  })
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const [sortColumn, setSortColumn] = useState<SortColumn>(null)
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')
  const [settingsOpen, setSettingsOpen] = useState(false)
  // SUDARSHAN: when the shown book was asked for, so only newer ticks move its P&L.
  const [bookAt, setBookAt] = useState<number | null>(null)
  const beginRead = useLatestRead()
  // SUDARSHAN M6.9: a phone's cards subscribe themselves, drawn once a frame,
  // so this page's own subscription is off there and a tick never redraws it.
  const phone = usePhoneLayout()

  // Centralized real-time price hook with WebSocket + MultiQuotes fallback
  // Automatically pauses when tab is hidden
  const {
    data: enhancedPositions,
    feed,
    calendar,
  } = useLivePrice(positions, {
    enabled: positions.length > 0 && !phone,
    useMultiQuotesFallback: true,
    pauseWhenHidden: true,
    snapshotAt: bookAt,
  })
  // SUDARSHAN: the status line above the table and the rows to grey.
  const { isOpen: marketOpen, anyOpen: anyMarketOpen } = useMarketOpen(calendar)
  const { line, now, stale: rowStale } = useBookFeed(enhancedPositions, feed, marketOpen)
  const tableStale = (line.kind === 'delayed' || line.kind === 'connecting') && line.stale

  // Load preferences from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        const prefs: Preferences = JSON.parse(saved)
        if (prefs.grouping) setGrouping(prefs.grouping)
        if (prefs.filters)
          setFilters({
            product: prefs.filters.product || [],
            direction: prefs.filters.direction || [],
            exchange: prefs.filters.exchange || [],
          })
      }
    } catch (_e) {}
  }, [])

  // Save preferences to localStorage
  const savePreferences = useCallback(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ grouping, filters }))
  }, [grouping, filters])

  useEffect(() => {
    savePreferences()
  }, [savePreferences])

  const fetchPositions = useCallback(
    async (showRefresh = false) => {
      if (!apiKey) {
        setIsLoading(false)
        return
      }

      if (showRefresh) setIsRefreshing(true)

      // SUDARSHAN: an answer a newer read has overtaken is dropped.
      const isLatest = beginRead()
      const askedAt = Date.now()
      try {
        const response = await tradingApi.getPositions(apiKey)
        if (!isLatest()) return
        if (response.status === 'success' && response.data) {
          setPositions(response.data)
          setBookAt(askedAt)
          setError(null)
        } else {
          setError(response.message || 'Failed to fetch positions')
        }
      } catch {
        if (isLatest()) setError('Failed to fetch positions')
      } finally {
        if (isLatest()) {
          setIsLoading(false)
          setIsRefreshing(false)
        }
      }
    },
    [apiKey, beginRead]
  )

  // Initial fetch and visibility-aware polling
  // Pauses polling when tab is hidden to save resources
  // SUDARSHAN: the first read only; coming back to the page is useBookSync's.
  useEffect(() => {
    fetchPositions()
  }, [fetchPositions])

  // SUDARSHAN: re-read after every order event including the broker's fills,
  // rejections and square-offs (order_update), on a Socket.IO reconnect, on
  // coming back after 15 s, and every 45 s while a market is open. The
  // "being refreshed" note shows while a read on coming back runs, and only then.
  const onReturnRead = useCallback((read: Promise<void>) => {
    setShowStaleWarning(true)
    void read.finally(() => setShowStaleWarning(false))
  }, [])
  useBookSync(fetchPositions, { marketOpen: anyMarketOpen, onReturnRead })

  // Auto-dismiss stale data warning after 5 seconds
  useEffect(() => {
    if (!showStaleWarning) return
    const timeout = setTimeout(() => setShowStaleWarning(false), 5000)
    return () => clearTimeout(timeout)
  }, [showStaleWarning])

  // Listen for mode changes (live/analyze) and refresh data
  useEffect(() => {
    const unsubscribe = onModeChange(() => {
      fetchPositions()
    })
    return () => unsubscribe()
  }, [fetchPositions])

  // Get group key for a position
  const getGroupKey = useCallback(
    (pos: Position): string => {
      const exchange = pos.exchange
      const product = pos.product

      if (exchange === 'NSE' || exchange === 'BSE') {
        if (product === 'CNC') return 'Equity (Delivery)'
        if (product === 'MIS') return 'Equity (Intraday)'
      }

      const parsed = parseSymbol(pos.symbol, exchange)

      if (grouping === 'underlying') {
        return parsed.underlying
      } else if (grouping === 'underlying_expiry') {
        return parsed.expiry ? `${parsed.underlying} - ${parsed.expiry}` : parsed.underlying
      }

      return parsed.underlying
    },
    [grouping]
  )

  // Filter positions (use enhancedPositions for real-time LTP/PnL)
  const filteredPositions = useMemo(() => {
    return enhancedPositions.filter((pos) => {
      if (filters.product.length > 0 && !filters.product.includes(pos.product)) return false

      const qty = pos.quantity || 0
      if (filters.direction.length > 0) {
        const isLong = qty > 0
        const isShort = qty < 0
        if (filters.direction.includes('LONG') && !filters.direction.includes('SHORT') && !isLong)
          return false
        if (filters.direction.includes('SHORT') && !filters.direction.includes('LONG') && !isShort)
          return false
      }

      if (filters.exchange.length > 0 && !filters.exchange.includes(pos.exchange)) return false

      return true
    })
  }, [enhancedPositions, filters])

  // Sort positions
  const sortedPositions = useMemo(() => {
    if (sortColumn === null) return filteredPositions

    return [...filteredPositions].sort((a, b) => {
      let aVal: string | number
      let bVal: string | number

      switch (sortColumn) {
        case 0:
          aVal = a.symbol
          bVal = b.symbol
          break
        case 3:
          aVal = a.quantity || 0
          bVal = b.quantity || 0
          break
        case 4:
          aVal = a.average_price || 0
          bVal = b.average_price || 0
          break
        case 6:
          aVal = a.pnl || 0
          bVal = b.pnl || 0
          break
        case 7:
          aVal = calculatePnlPercent(a)
          bVal = calculatePnlPercent(b)
          break
        default:
          return 0
      }

      if (typeof aVal === 'string') {
        return sortDirection === 'asc'
          ? aVal.localeCompare(bVal as string)
          : (bVal as string).localeCompare(aVal)
      }
      return sortDirection === 'asc'
        ? (aVal as number) - (bVal as number)
        : (bVal as number) - (aVal as number)
    })
  }, [filteredPositions, sortColumn, sortDirection])

  // Group positions
  const groupedPositions = useMemo(() => {
    if (grouping === 'none') {
      return { _all: sortedPositions }
    }

    // SUDARSHAN: the priced rows' own type, so their tick fields reach the table.
    const groups: Record<string, typeof sortedPositions> = {}
    sortedPositions.forEach((pos) => {
      const groupKey = getGroupKey(pos)
      if (!groups[groupKey]) groups[groupKey] = []
      groups[groupKey].push(pos)
    })

    return groups
  }, [sortedPositions, grouping, getGroupKey])

  // Calculate stats
  const stats = useMemo(() => {
    const long = filteredPositions.filter((p) => (p.quantity || 0) > 0).length
    const short = filteredPositions.filter((p) => (p.quantity || 0) < 0).length
    // Only count positions with non-zero quantity as "open"
    const total = long + short
    const totalPnl = filteredPositions.reduce((sum, p) => sum + (p.pnl || 0), 0)
    return { total, long, short, totalPnl }
  }, [filteredPositions])

  const handleSort = (column: SortColumn) => {
    if (sortColumn === column) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc')
    } else {
      setSortColumn(column)
      setSortDirection('asc')
    }
  }

  const toggleFilter = (type: keyof FilterState, value: string) => {
    setFilters((prev) => {
      const arr = prev[type]
      const index = arr.indexOf(value)
      if (index > -1) {
        return { ...prev, [type]: arr.filter((v) => v !== value) }
      }
      return { ...prev, [type]: [...arr, value] }
    })
  }

  const clearFilters = () => {
    setFilters({ product: [], direction: [], exchange: [] })
    setGrouping('none')
    setCollapsedGroups(new Set())
  }

  const toggleGroup = (groupKey: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(groupKey)) {
        next.delete(groupKey)
      } else {
        next.add(groupKey)
      }
      return next
    })
  }

  const hasActiveFilters =
    filters.product.length > 0 ||
    filters.direction.length > 0 ||
    filters.exchange.length > 0 ||
    grouping !== 'none'

  // SUDARSHAN M6.9: each returns what became of the exit, so the phone holds its
  // Exit (src/sudarshan/books/sent.ts). An answer that may have reached the broker is
  // "not confirmed", never "failed": a second exit would reverse the position.
  const handleClosePosition = async (position: Position): Promise<SendOutcome | SentAnswer> => {
    try {
      const response = await tradingApi.closePosition(
        position.symbol,
        position.exchange,
        position.product
      )
      if (response.status === 'success') {
        // SUDARSHAN: says what was sent, from the position, in the confirmation's words; the
        // server's sentence read "BUY Order Placed ... Qty: 0" for a long being sold.
        const held = Number(position.quantity) || 0
        const contract = spokenContract(position.symbol, position.exchange)
        showToast.success(
          held === 0
            ? `Exit sent: ${contract}`
            : `Exit sent: ${held > 0 ? 'sell' : 'buy'} ${units(Math.abs(held))} ${contract}`,
          'positions'
        )
        fetchPositions(true)
        // The orders this placed are the exit's own, not any other on its side.
        return { outcome: 'sent', orderids: sentOrderIds(response) }
      }
      const held = answeredHeld(response)
      if (held) {
        showToast.warning(heldText('exit', held, response), 'positions')
        fetchPositions(true)
        return held
      }
      showToast.error(response.message || 'Failed to close position', 'positions')
    } catch (err) {
      const outcome = thrownOutcome(err)
      if (outcome !== 'refused') {
        showToast.warning(heldText('exit', outcome, answerBody(err)), 'positions')
        fetchPositions(true)
        return outcome
      }
      showToast.error(refusalReason(err) ?? 'Failed to close position', 'positions')
      // An earlier exit for it is still being placed (the app's 409): that one holds the row.
      if (stillPlacing(err)) return 'placing'
    }
    return 'refused'
  }

  // Its result shares one toast with the close_position_event the server sends for it.
  // SUDARSHAN M6.9: and each position settles by what became of it (the answer's
  // `positions`): one Exit all sent nothing for lets go and says why on its card.
  const handleCloseAllPositions = async (): Promise<SendOutcome | SentAnswer> => {
    const once = { id: EXIT_ALL_TOAST }
    const withEach = (outcome: SendOutcome, body: unknown): SendOutcome | SentAnswer => {
      const each = exitAllEach(body)
      return each ? { outcome, each } : outcome
    }
    try {
      const response = await tradingApi.closeAllPositions()
      if (response.status === 'success') {
        // Toast handled by close_position_event socket
        fetchPositions(true)
        return withEach('sent', response)
      }
      const held = answeredHeld(response)
      if (held) {
        showToast.warning(heldText('exitAll', held, response), 'positions', once)
        fetchPositions(true)
        return withEach(held, response)
      }
      showToast.error(response.message || 'Failed to close all positions', 'positions', once)
      return withEach('refused', response)
    } catch (err) {
      const outcome = thrownOutcome(err)
      if (outcome !== 'refused') {
        showToast.warning(heldText('exitAll', outcome, answerBody(err)), 'positions', once)
        fetchPositions(true)
        return withEach(outcome, answerBody(err))
      }
      showToast.error(refusalReason(err) ?? 'Failed to close all positions', 'positions', once)
      // Each position said for itself (nothing went, each with its reason); else one still
      // being placed (the app's 409 with no positions) holds every row.
      if (exitAllEach(answerBody(err))) return withEach('refused', answerBody(err))
      if (stillPlacing(err)) return 'placing'
    }
    return 'refused'
  }

  // SUDARSHAN M6.9: the exit hold's side of this page: each book read is told to it, and
  // the table's Close and Close All hold as the phone's Exit does (a phone turned on its
  // side shows this table).
  const tableExits = usePositionExits({
    onClose: handleClosePosition,
    onCloseAll: handleCloseAllPositions,
    positions,
    bookAt,
    loading: isLoading,
    apiKey,
  })

  const exportToCSV = () => {
    if (filteredPositions.length === 0) {
      showToast.error('No data to export', 'system')
      return
    }

    try {
      const headers = [
        'Symbol',
        'Exchange',
        ...(isCrypto ? [] : ['Product']),
        'Quantity',
        'Avg Price',
        'LTP',
        'P&L',
        'P&L %',
      ]
      const rows = filteredPositions.map((p) => [
        sanitizeCSV(p.symbol),
        sanitizeCSV(p.exchange),
        ...(isCrypto ? [] : [sanitizeCSV(p.product)]),
        sanitizeCSV(p.quantity),
        sanitizeCSV(p.average_price),
        sanitizeCSV(p.ltp),
        sanitizeCSV(p.pnl),
        sanitizeCSV(calculatePnlPercent(p)),
      ])

      const csv = [headers, ...rows].map((row) => row.join(',')).join('\n')
      const blob = new Blob([csv], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const filename = `positions_${new Date().toISOString().split('T')[0]}.csv`
      a.download = filename
      a.click()
      // Revoke the object URL to free memory
      URL.revokeObjectURL(url)
      showToast.success(`Downloaded ${filename}`, 'clipboard')
    } catch {
      showToast.error('Failed to export CSV', 'system')
    }
  }

  const isProfit = (value: number) => value >= 0

  const FilterChip = ({
    type,
    value,
    label,
  }: {
    type: keyof FilterState
    value: string
    label: string
  }) => (
    <Button
      variant={filters[type].includes(value) ? 'default' : 'outline'}
      size="sm"
      className={cn(
        'rounded-full',
        filters[type].includes(value) && 'bg-primary text-primary-foreground hover:bg-primary/90'
      )}
      onClick={() => toggleFilter(type, value)}
    >
      {label}
    </Button>
  )

  const SortableHeader = ({
    column,
    label,
    className,
  }: {
    column: SortColumn
    label: string
    className?: string
  }) => (
    <TableHead
      className={cn('cursor-pointer hover:bg-muted/50 select-none', className)}
      onClick={() => handleSort(column)}
    >
      <div
        className={cn(
          'flex items-center gap-1 w-full',
          className?.includes('text-right') && 'justify-end'
        )}
      >
        {label}
        <ArrowUpDown className="h-3 w-3 opacity-50" />
      </div>
    </TableHead>
  )

  // Calculate group stats
  const calculateGroupStats = (positions: Position[]) => {
    let totalPnl = 0
    let totalInvestment = 0

    positions.forEach((pos) => {
      totalPnl += pos.pnl || 0
      const avgPrice = pos.average_price || 0
      const qty = Math.abs(pos.quantity || 0)
      totalInvestment += avgPrice * qty
    })

    const pnlPercent = totalInvestment > 0 ? (totalPnl / totalInvestment) * 100 : 0
    return { totalPnl, pnlPercent, count: positions.length }
  }

  // Sort group keys
  const sortedGroupKeys = Object.keys(groupedPositions).sort((a, b) => {
    if (a.startsWith('Equity') && !b.startsWith('Equity')) return -1
    if (!a.startsWith('Equity') && b.startsWith('Equity')) return 1
    return a.localeCompare(b)
  })

  // SUDARSHAN M6.9: on a phone, a card per position instead of the wide table.
  // Every position is shown (the filters here are the wide table's), and Exit
  // and Exit all are this page's own calls.
  if (phone) {
    return (
      <PositionCards
        positions={positions}
        bookAt={bookAt}
        loading={isLoading}
        error={error}
        refreshing={isRefreshing || showStaleWarning}
        apiKey={apiKey}
        marketOpen={marketOpen}
        onRefresh={fetchPositions}
        onExit={handleClosePosition}
        onExitAll={handleCloseAllPositions}
      />
    )
  }

  return (
    <div className="space-y-6">
      {/* Stale Data Warning */}
      {showStaleWarning && (
        <Alert variant="default" className={WARNING_BOX}>
          <AlertTriangle className={cn('h-4 w-4', WARNING_INK)} />
          <AlertDescription className={WARNING_INK}>
            Data is being refreshed after tab was inactive...
          </AlertDescription>
        </Alert>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1 className="text-3xl font-bold tracking-tight">Positions</h1>
            {/* SUDARSHAN: "Real-time" only while ticks arrive, never "Live" for the feed. */}
            {positions.length > 0 && <LiveLine line={line} now={now} />}
          </div>
          <p className="text-muted-foreground">Monitor and manage your active trading positions</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Settings Button */}
          <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
            <DialogTrigger asChild>
              <Button
                variant={hasActiveFilters ? 'default' : 'outline'}
                size="sm"
                className="relative"
              >
                <Settings2 className="h-4 w-4 mr-2" />
                Settings
                {hasActiveFilters && (
                  <span
                    className={cn('absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full', FILTER_DOT)}
                  />
                )}
              </Button>
            </DialogTrigger>
            <DialogContent className="max-w-md">
              <DialogHeader>
                <DialogTitle>Position Settings</DialogTitle>
                <DialogDescription>Configure grouping and filters</DialogDescription>
              </DialogHeader>

              <div className="space-y-6 py-4">
                {/* Grouping */}
                <div className="space-y-3">
                  <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Grouping
                  </Label>
                  <div className="space-y-2">
                    {[
                      { value: 'none', label: 'None' },
                      { value: 'underlying', label: 'Underlying' },
                      { value: 'underlying_expiry', label: 'Underlying & Expiry' },
                    ].map((opt) => (
                      <label
                        key={opt.value}
                        className={cn(
                          'flex items-center gap-3 cursor-pointer p-2 rounded hover:bg-muted',
                          grouping === opt.value &&
                            'bg-[color:var(--brand-tint)] border border-[color:var(--brand)]'
                        )}
                      >
                        <input
                          type="radio"
                          name="grouping"
                          checked={grouping === opt.value}
                          onChange={() => {
                            setGrouping(opt.value as GroupingType)
                            setCollapsedGroups(new Set())
                          }}
                          className="accent-[color:var(--brand)]"
                        />
                        <span
                          className={cn(
                            grouping === opt.value && 'text-[color:var(--brand)] font-semibold'
                          )}
                        >
                          {opt.label}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="border-t" />

                {/* Product Type */}
                {!isCrypto && (
                  <div className="space-y-3">
                    <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Product Type
                    </Label>
                    <div className="flex flex-wrap gap-2">
                      <FilterChip type="product" value="CNC" label="CNC" />
                      <FilterChip type="product" value="MIS" label="MIS" />
                      <FilterChip type="product" value="NRML" label="NRML" />
                    </div>
                  </div>
                )}

                {/* Direction */}
                <div className="space-y-3">
                  <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Direction
                  </Label>
                  <div className="flex flex-wrap gap-2">
                    <FilterChip type="direction" value="LONG" label="Long" />
                    <FilterChip type="direction" value="SHORT" label="Short" />
                  </div>
                </div>

                {/* Exchange */}
                <div className="space-y-3">
                  <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Exchange
                  </Label>
                  <div className="flex flex-wrap gap-2">
                    <FilterChip type="exchange" value="NSE" label="NSE" />
                    <FilterChip type="exchange" value="BSE" label="BSE" />
                    <FilterChip type="exchange" value="NFO" label="NFO" />
                    <FilterChip type="exchange" value="BFO" label="BFO" />
                    <FilterChip type="exchange" value="MCX" label="MCX" />
                    <FilterChip type="exchange" value="CDS" label="CDS" />
                  </div>
                </div>
              </div>

              <DialogFooter>
                <Button variant="ghost" onClick={clearFilters}>
                  Clear All
                </Button>
                <Button onClick={() => setSettingsOpen(false)}>Done</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchPositions(true)}
            disabled={isRefreshing}
          >
            <RefreshCw className={cn('h-4 w-4 mr-2', isRefreshing && 'animate-spin')} />
            Refresh
          </Button>

          <Button variant="outline" size="sm" onClick={exportToCSV}>
            <Download className="h-4 w-4 mr-2" />
            Export
          </Button>

          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="destructive" size="sm" disabled={stats.total === 0}>
                <X className="h-4 w-4 mr-2" />
                Close All
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Close All Positions?</AlertDialogTitle>
                <AlertDialogDescription>
                  {/* SUDARSHAN M6.9: what became of an earlier exit whose time ran out. */}
                  {tableExits.allWarning && <WarningLine text={tableExits.allWarning} />}
                  This will close all {stats.total} open positions at market price. This action
                  cannot be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={tableExits.closeAll}>Close All</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      {/* Active Filters Bar */}
      {hasActiveFilters && (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted-foreground">Active Filters:</span>
          {grouping !== 'none' && (
            <Badge
              variant="secondary"
              className="bg-[color:var(--brand-tint)] text-[color:var(--brand)] border-transparent"
            >
              Grouped: {grouping === 'underlying' ? 'Underlying' : 'Underlying & Expiry'}
            </Badge>
          )}
          {!isCrypto &&
            filters.product.map((v) => (
              <Badge
                key={v}
                variant="secondary"
                className="bg-[color:var(--brand-tint)] text-[color:var(--brand)] border-transparent"
              >
                {v}
              </Badge>
            ))}
          {filters.direction.map((v) => (
            <Badge
              key={v}
              variant="secondary"
              className="bg-[color:var(--brand-tint)] text-[color:var(--brand)] border-transparent"
            >
              {v}
            </Badge>
          ))}
          {filters.exchange.map((v) => (
            <Badge
              key={v}
              variant="secondary"
              className="bg-[color:var(--brand-tint)] text-[color:var(--brand)] border-transparent"
            >
              {v}
            </Badge>
          ))}
          <Button variant="outline" size="sm" className={DANGER_OUTLINE} onClick={clearFilters}>
            Clear All
          </Button>
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Open Positions</CardDescription>
            <CardTitle className="text-2xl">{stats.total}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Long</CardDescription>
            <CardTitle className={cn('text-2xl', BUY_INK)}>{stats.long}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Short</CardDescription>
            <CardTitle className={cn('text-2xl', SELL_INK)}>{stats.short}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Total P&L</CardDescription>
            <CardTitle
              className={cn(
                'text-2xl',
                // SUDARSHAN: greyed while its ticks are stale; the sign stays.
                tableStale ? 'text-muted-foreground' : pnlTone(stats.totalPnl)
              )}
            >
              {/* SUDARSHAN: a plus on a gain, so a greyed figure still says which way. */}
              {stats.totalPnl > 0 ? '+' : ''}
              {formatCurrency(stats.totalPnl)}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>

      {/* Positions Table */}
      <Card>
        <CardContent className="py-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin" />
            </div>
          ) : error ? (
            <div className="text-center py-12 text-muted-foreground">{error}</div>
          ) : filteredPositions.length === 0 ? (
            <EmptyState
              icon={ChartCandlestick}
              title="No positions match your filters"
              description="Try adjusting or clearing your filters to see results."
              action={
                hasActiveFilters ? (
                  <Button variant="ghost" size="sm" onClick={clearFilters}>
                    Clear Filters
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortableHeader column={0} label="Symbol" className="w-[140px]" />
                    <TableHead className="w-[80px]">Exchange</TableHead>
                    {!isCrypto && <TableHead className="w-[80px]">Product</TableHead>}
                    <SortableHeader column={3} label="Qty" className="w-[80px] text-right" />
                    <SortableHeader column={4} label="Avg Price" className="w-[120px] text-right" />
                    <TableHead className="w-[120px] text-right">LTP</TableHead>
                    <SortableHeader column={6} label="P&L" className="w-[120px] text-right" />
                    <SortableHeader column={7} label="P&L %" className="w-[100px] text-right" />
                    <TableHead className="w-[60px] text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedGroupKeys.map((groupKey) => {
                    const groupPositions = groupedPositions[groupKey]
                    const isCollapsed = collapsedGroups.has(groupKey)
                    const groupStats = calculateGroupStats(groupPositions)

                    return (
                      <React.Fragment key={groupKey}>
                        {/* Group Header Row */}
                        {grouping !== 'none' && (
                          <TableRow
                            className="bg-muted/50 cursor-pointer hover:bg-muted"
                            onClick={() => toggleGroup(groupKey)}
                          >
                            <TableCell colSpan={6}>
                              <div className="flex items-center gap-3 py-1 font-semibold">
                                {isCollapsed ? (
                                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                                ) : (
                                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                                )}
                                <span>{groupKey}</span>
                                <Badge variant="outline" className="text-xs">
                                  {groupStats.count}
                                </Badge>
                              </div>
                            </TableCell>
                            <TableCell
                              className={cn('text-right font-bold', pnlTone(groupStats.totalPnl))}
                            >
                              {groupStats.totalPnl >= 0 ? '+' : ''}
                              {formatCurrency(groupStats.totalPnl)}
                            </TableCell>
                            <TableCell
                              className={cn(
                                'text-right font-semibold',
                                pnlTone(groupStats.pnlPercent)
                              )}
                            >
                              {groupStats.pnlPercent >= 0 ? '+' : ''}
                              {groupStats.pnlPercent.toFixed(2)}%
                            </TableCell>
                            <TableCell />
                          </TableRow>
                        )}

                        {/* Position Rows */}
                        {!isCollapsed &&
                          groupPositions.map((position, index) => {
                            // A broker keeps reporting a position it has
                            // squared off, with quantity 0 and its realised
                            // P&L. Those rows stay - the figure is the point -
                            // but there is nothing left to close on them.
                            //
                            // Coerced, not compared directly: the quantity is
                            // typed as a number but does not always arrive as
                            // one. Zerodha's position mapping defaults it to
                            // the string "0", and a strict `!== 0` reads that
                            // as an open position, which is the exact row this
                            // guard exists to catch. calculatePnlPercent above
                            // coerces for the same reason.
                            const quantity = Number(position.quantity) || 0
                            const isOpen = quantity !== 0
                            // SUDARSHAN: a price that should be ticking and is not.
                            const stale = rowStale(position)

                            return (
                              <TableRow key={`${position.symbol}-${position.exchange}-${index}`}>
                                <TableCell className="w-[140px] font-medium">
                                  <BookSymbolLink
                                    symbol={position.symbol}
                                    exchange={position.exchange}
                                  />
                                </TableCell>
                                <TableCell className="w-[80px]">
                                  <Badge variant="outline" className={QUIET_CHIP}>
                                    {position.exchange}
                                  </Badge>
                                </TableCell>
                                {!isCrypto && (
                                  <TableCell className="w-[80px]">
                                    <Badge variant="outline" className={QUIET_CHIP}>
                                      {position.product}
                                    </Badge>
                                  </TableCell>
                                )}
                                <TableCell
                                  className={cn(
                                    'w-[80px] text-right font-medium',
                                    // A squared-off position is neither long nor
                                    // short. Colouring its 0 red read as a short.
                                    !isOpen
                                      ? 'text-muted-foreground'
                                      : quantity > 0
                                        ? BUY_INK
                                        : SELL_INK
                                  )}
                                >
                                  {position.quantity}
                                </TableCell>
                                <TableCell className="w-[120px] text-right font-mono">
                                  {formatCurrency(position.average_price)}
                                </TableCell>
                                <TableCell className="w-[120px] text-right font-mono">
                                  {/* SUDARSHAN: the last tick's direction, greyed when stale. */}
                                  <TickPrice
                                    value={position.ltp}
                                    direction={position._tick}
                                    stale={stale}
                                    format={formatCurrency}
                                  />
                                </TableCell>
                                <TableCell
                                  className={cn(
                                    'w-[120px] text-right font-medium',
                                    stale ? 'text-muted-foreground' : pnlTone(position.pnl)
                                  )}
                                >
                                  <div className="flex items-center justify-end gap-1">
                                    {isProfit(position.pnl) ? (
                                      <TrendingUp className="h-4 w-4" />
                                    ) : (
                                      <TrendingDown className="h-4 w-4" />
                                    )}
                                    {signedMoney(position.pnl, formatCurrency)}
                                  </div>
                                </TableCell>
                                <TableCell
                                  className={cn(
                                    'w-[100px] text-right',
                                    stale
                                      ? 'text-muted-foreground'
                                      : pnlTone(calculatePnlPercent(position))
                                  )}
                                >
                                  {calculatePnlPercent(position) >= 0 ? '+' : ''}
                                  {calculatePnlPercent(position).toFixed(2)}%
                                </TableCell>
                                <TableCell className="w-[60px] text-right">
                                  {isOpen && (
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      className="text-destructive hover:text-destructive hover:bg-destructive/10"
                                      onClick={() => tableExits.close(position)}
                                      aria-label={`Close ${position.symbol} position`}
                                    >
                                      <X className="h-4 w-4" />
                                    </Button>
                                  )}
                                </TableCell>
                              </TableRow>
                            )
                          })}
                      </React.Fragment>
                    )
                  })}
                </TableBody>
                <TableFooter>
                  <TableRow className="bg-muted/50">
                    <TableCell colSpan={6} className="text-right text-muted-foreground">
                      Total P&L:
                    </TableCell>
                    <TableCell
                      className={cn(
                        'w-[120px] text-right font-bold',
                        // SUDARSHAN: greyed while its ticks are stale; the sign stays.
                        tableStale ? 'text-muted-foreground' : pnlTone(stats.totalPnl)
                      )}
                    >
                      {stats.totalPnl >= 0 ? '+' : ''}
                      {formatCurrency(stats.totalPnl)}
                    </TableCell>
                    <TableCell colSpan={2} />
                  </TableRow>
                </TableFooter>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
      {/* SUDARSHAN M6.9: Close offered again after an earlier exit's time ran out asks first. */}
      {tableExits.dialog}
    </div>
  )
}
