import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ClipboardList,
  Clock,
  Download,
  FilterX,
  Loader2,
  Pencil,
  RefreshCw,
  Settings2,
  X,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { tradingApi } from '@/api/trading'
import GttTab from '@/components/trading/GttTab'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { type PriceableItem, useLivePrice } from '@/hooks/useLivePrice'
import { useLiveQuote } from '@/hooks/useLiveQuote'
// Note: AlertDialog still used for Cancel All Orders
import { useSupportedExchanges } from '@/hooks/useSupportedExchanges'
import { cn, makeFormatCurrency, sanitizeCSV } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { onModeChange, useThemeStore } from '@/stores/themeStore'
// SUDARSHAN M6.9: on a phone the orders are cards (src/sudarshan/books/cards).
import { OrderCards } from '@/sudarshan/books/cards/OrderCards'
// SUDARSHAN: live status, the LTP of working orders, the book re-read (src/sudarshan/books).
import { useBookFeed } from '@/sudarshan/books/feed'
import { TickPrice } from '@/sudarshan/books/LiveFigures'
import { noteCancelAllFromApp } from '@/sudarshan/books/orderAlerts'
import {
  foldOrderUpdate,
  frameFitsMode,
  holdPushed,
  isFinalStatus,
  noteOrderBook,
  pushedStatuses,
  useOrderUpdates,
} from '@/sudarshan/books/orderFold'
import { QuoteStatus } from '@/sudarshan/books/QuoteStatus'
// SUDARSHAN M6.9: what became of a cancel: sent, refused, or not confirmed (no answer).
import {
  appAnswered,
  NOT_CONFIRMED,
  refusalReason,
  thrownOutcome,
} from '@/sudarshan/books/sendOutcome'
import type { SendOutcome } from '@/sudarshan/books/sent'
import { useBookSync, useLatestRead } from '@/sudarshan/books/useBookSync'
// SUDARSHAN M4.15: on a phone, a symbol opens the phone chart (src/sudarshan/chart).
import { BookSymbolLink } from '@/sudarshan/chart/BookSymbolLink'
import { usePhoneLayout } from '@/sudarshan/chart/usePhoneLayout'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import type { Order, OrderStats } from '@/types/trading'
import { showToast } from '@/utils/toast'

// Sort configuration types
type SortKey = 'timestamp' | 'symbol' | 'action' | 'order_status' | 'price'
interface SortConfig {
  key: SortKey
  direction: 'asc' | 'desc'
}

// SUDARSHAN: the events that re-read this book are useBookSync's BOOK_EVENTS,
// which add the broker's own order updates (fills, rejections, square-offs).

/**
 * Helper to convert various broker timestamp formats into a sortable number.
 * Ensures chronological accuracy for non-ISO formats.
 */
function parseTimestamp(timestamp: string): number {
  if (!timestamp) return 0

  let date = new Date(timestamp)

  // If invalid, try "HH:MM:SS DD-MM-YYYY" (Flattrade/Shoonya/Zebu/Firstock norentm format)
  if (Number.isNaN(date.getTime())) {
    const norentm = timestamp.match(/^(\d{2}:\d{2}:\d{2})\s+(\d{2})-(\d{2})-(\d{4})$/)
    if (norentm) {
      date = new Date(`${norentm[4]}-${norentm[3]}-${norentm[2]}T${norentm[1]}`)
    }
  }

  // If invalid, try "DD-MM-YYYY HH:MM:SS"
  if (Number.isNaN(date.getTime())) {
    const ddmmyyyy = timestamp.match(/^(\d{2})-(\d{2})-(\d{4})\s+(\d{2}:\d{2}:\d{2})$/)
    if (ddmmyyyy) {
      date = new Date(`${ddmmyyyy[3]}-${ddmmyyyy[2]}-${ddmmyyyy[1]}T${ddmmyyyy[4]}`)
    }
  }

  return date.getTime() || 0
}

function formatTime(timestamp: string): string {
  if (!timestamp) return '-'

  const timeValue = parseTimestamp(timestamp)
  if (timeValue === 0) {
    // Last resort: extract HH:MM:SS if embedded in the string
    const timeMatch = timestamp.match(/(\d{2}:\d{2}:\d{2})/)
    return timeMatch ? timeMatch[1] : timestamp
  }

  const date = new Date(timeValue)
  return date.toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

const statusConfig: Record<string, { icon: typeof CheckCircle2; color: string; label: string }> = {
  complete: { icon: CheckCircle2, color: 'text-[color:var(--up)]', label: 'complete' },
  rejected: { icon: XCircle, color: 'text-[color:var(--down)]', label: 'rejected' },
  cancelled: { icon: XCircle, color: 'text-[color:var(--neutral)]', label: 'cancelled' },
  open: { icon: Clock, color: 'text-[color:var(--warning-text)]', label: 'open' },
}

// SUDARSHAN (M1.18): the theme's colours instead of Tailwind's fixed palette,
// so BUY and SELL read at 5:1 or better and the stats fit three to a row on a
// phone. White on green-500, as before, was 2.3:1.
const SIDE_BADGE: Record<string, string> = {
  BUY: 'border-transparent bg-[color:var(--buy-solid)] text-white',
  SELL: 'border-transparent bg-[color:var(--sell-solid)] text-white',
}
const STAT_CARD = 'gap-0 py-3 sm:py-6'
const STAT_HEADER = 'gap-1 px-3 pb-0 sm:px-6 sm:pb-2'
const STAT_LABEL = 'text-xs sm:text-sm'
const STAT_VALUE = 'sd-display text-[1.75rem] leading-none font-semibold'

export default function OrderBook() {
  const { apiKey, user } = useAuthStore()
  const { isCrypto } = useSupportedExchanges()
  const formatCurrency = useMemo(() => makeFormatCurrency(user?.broker), [user?.broker])
  const [orders, setOrders] = useState<Order[]>([])
  const [stats, setStats] = useState<OrderStats | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Filter state
  const [statusFilter, setStatusFilter] = useState<string[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)

  // Sort state - Default: most recent first
  const [sortConfig, setSortConfig] = useState<SortConfig>({
    key: 'timestamp',
    direction: 'desc',
  })

  // Modify order state
  const [modifyDialogOpen, setModifyDialogOpen] = useState(false)
  const [modifyingOrder, setModifyingOrder] = useState<Order | null>(null)
  // SUDARSHAN: the modify dialog's quote ticks while it is open, instead of
  // being read once (useLiveQuote: one REST snapshot, then the socket).
  const {
    data: liveQuote,
    isLoading: isLoadingQuotes,
    feed: quoteFeed,
    calendar: quoteCalendar,
  } = useLiveQuote(modifyingOrder?.symbol ?? '', modifyingOrder?.exchange ?? '', {
    enabled: modifyDialogOpen && !!modifyingOrder,
    mode: 'Depth',
  })
  const quotes = liveQuote.ltp !== undefined ? liveQuote : null
  // SUDARSHAN: a figure the quote does not carry yet reads "-", never 0.
  const quoteFigure = (value: number | undefined) =>
    typeof value === 'number' && value > 0 ? formatCurrency(value) : '-'
  const [modifyForm, setModifyForm] = useState({
    quantity: 0,
    price: 0,
    trigger_price: 0,
    pricetype: 'MARKET' as string,
    product: 'MIS' as string,
  })

  // Filter and Sort orders
  const sortedAndFilteredOrders = useMemo(() => {
    // 1. Filter Logic
    const filtered =
      statusFilter.length === 0
        ? orders
        : orders.filter((order) => statusFilter.includes(order.order_status))

    // 2. Sort Logic
    return [...filtered].sort((a, b) => {
      const aValue = a[sortConfig.key]
      const bValue = b[sortConfig.key]

      if (sortConfig.key === 'timestamp') {
        const aTime = parseTimestamp(aValue as string)
        const bTime = parseTimestamp(bValue as string)
        return sortConfig.direction === 'asc' ? aTime - bTime : bTime - aTime
      }

      // Numeric comparison for Price (broker payloads may carry it as string)
      if (sortConfig.key === 'price') {
        const aPrice = Number(aValue) || 0
        const bPrice = Number(bValue) || 0
        return sortConfig.direction === 'asc' ? aPrice - bPrice : bPrice - aPrice
      }

      // Standard string comparison for Symbol, Action, and Status
      if (aValue < bValue) return sortConfig.direction === 'asc' ? -1 : 1
      if (aValue > bValue) return sortConfig.direction === 'asc' ? 1 : -1

      // Symbol sort groups orders by instrument; order each group by price
      // so the book reads as a ladder per instrument (issue #1339 part 2).
      if (sortConfig.key === 'symbol') {
        return (Number(a.price) || 0) - (Number(b.price) || 0)
      }
      return 0
    })
  }, [orders, statusFilter, sortConfig])

  const requestSort = (key: SortKey) => {
    setSortConfig((prev) => ({
      key,
      direction: prev.key === key && prev.direction === 'asc' ? 'desc' : 'asc',
    }))
  }

  const hasActiveFilters = statusFilter.length > 0

  const toggleStatusFilter = (status: string) => {
    setStatusFilter((prev) => {
      if (prev.includes(status)) {
        return prev.filter((s) => s !== status)
      }
      return [...prev, status]
    })
  }

  const clearFilters = () => {
    setStatusFilter([])
  }

  // SUDARSHAN: what the broker last pushed for each order (kept app-wide, so a
  // page opened just after a fill holds it too), held over a lagging re-read.
  const beginRead = useLatestRead()
  const appMode = useThemeStore((s) => s.appMode)

  const fetchOrders = useCallback(
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
        const response = await tradingApi.getOrders(apiKey)
        if (!isLatest()) return
        if (response.status === 'success' && response.data) {
          noteOrderBook(response.data.orders || [], askedAt)
          setOrders(holdPushed(response.data.orders || [], pushedStatuses(), Date.now()))
          setStats(response.data.statistics)
          setError(null)
        } else {
          setError(response.message || 'Failed to fetch orders')
        }
      } catch {
        if (isLatest()) setError('Failed to fetch orders')
      } finally {
        if (isLatest()) {
          setIsLoading(false)
          setIsRefreshing(false)
        }
      }
    },
    [apiKey, beginRead]
  )

  useEffect(() => {
    fetchOrders()
  }, [fetchOrders])

  // Refresh on order events instead of polling
  // SUDARSHAN: the broker's own updates change their row at once and are
  // confirmed by a re-read; every order event, a Socket.IO reconnect, coming
  // back after 15 s and a 45 s re-sync while a market is open re-read the book.
  useOrderUpdates((frame) => {
    if (!frameFitsMode(frame, appMode)) return
    setOrders((current) => foldOrderUpdate(current, frame))
  })

  // SUDARSHAN: the last price of every instrument with a working order.
  const working = useMemo(() => {
    const seen = new Map<string, PriceableItem>()
    for (const order of orders) {
      if (isFinalStatus(order.order_status)) continue
      seen.set(`${order.exchange}:${order.symbol}`, {
        symbol: order.symbol,
        exchange: order.exchange,
      })
    }
    return Array.from(seen.values())
  }, [orders])
  // SUDARSHAN M6.9: a phone's cards subscribe themselves, drawn once a frame,
  // so this page's own subscription is off there and a tick never redraws it.
  const phone = usePhoneLayout()
  const {
    data: workingPrices,
    feed: workingFeed,
    calendar,
  } = useLivePrice(working, { enabled: working.length > 0 && !phone })
  const { isOpen: marketOpen, anyOpen: anyMarketOpen } = useMarketOpen(calendar)
  const {
    line: ltpLine,
    now: ltpNow,
    stale: ltpStale,
  } = useBookFeed(workingPrices, workingFeed, marketOpen)
  const priceOf = useMemo(
    () => new Map(workingPrices.map((row) => [`${row.exchange}:${row.symbol}`, row])),
    [workingPrices]
  )

  useBookSync(fetchOrders, { marketOpen: anyMarketOpen })

  // Listen for mode changes (live/analyze) and refresh data
  useEffect(() => {
    const unsubscribe = onModeChange(() => {
      fetchOrders()
    })
    return () => unsubscribe()
  }, [fetchOrders])

  // SUDARSHAN M6.9: each returns what became of the cancel, so the phone holds its
  // Cancel (src/sudarshan/books/sent.ts). No answer is "not confirmed", never "failed".
  const handleCancelOrder = async (orderid: string): Promise<SendOutcome> => {
    try {
      const response = await tradingApi.cancelOrder(orderid)
      if (response.status === 'success') {
        showToast.success(`Order cancelled: ${orderid}`, 'orders')
        setTimeout(() => fetchOrders(true), 1000)
        return 'sent'
      } else if (!appAnswered(response)) {
        showToast.warning(NOT_CONFIRMED.cancel, 'orders')
        setTimeout(() => fetchOrders(true), 1000)
        return 'unconfirmed'
      } else {
        showToast.error(response.message || 'Failed to cancel order', 'orders')
      }
    } catch (error) {
      if (thrownOutcome(error) === 'unconfirmed') {
        showToast.warning(NOT_CONFIRMED.cancel, 'orders')
        setTimeout(() => fetchOrders(true), 1000)
        return 'unconfirmed'
      }
      showToast.error(refusalReason(error) ?? 'Failed to cancel order', 'orders')
    }
    return 'refused'
  }

  const handleCancelAllOrders = async (): Promise<SendOutcome> => {
    // SUDARSHAN: the orders working now are the ones this cancels; their cancels are not alerts.
    noteCancelAllFromApp()
    try {
      const response = await tradingApi.cancelAllOrders()
      if (response.status === 'success') {
        showToast.success(response.message || 'All orders cancelled', 'orders')
        // Delay refresh to allow broker to process cancellations
        setTimeout(() => fetchOrders(true), 2000)
        return 'sent'
      } else if (response.status === 'info') {
        showToast.info(response.message || 'No open orders to cancel', 'orders')
      } else if (!appAnswered(response)) {
        showToast.warning(NOT_CONFIRMED.cancelAll, 'orders')
        setTimeout(() => fetchOrders(true), 2000)
        return 'unconfirmed'
      } else {
        showToast.error(response.message || 'Failed to cancel all orders', 'orders')
      }
    } catch (error) {
      if (thrownOutcome(error) === 'unconfirmed') {
        showToast.warning(NOT_CONFIRMED.cancelAll, 'orders')
        setTimeout(() => fetchOrders(true), 2000)
        return 'unconfirmed'
      }
      showToast.error(refusalReason(error) ?? 'Failed to cancel all orders', 'orders')
    }
    return 'refused'
  }

  const openModifyDialog = async (order: Order) => {
    setModifyingOrder(order)
    setModifyForm({
      quantity: order.quantity,
      price: order.price,
      trigger_price: order.trigger_price,
      pricetype: order.pricetype,
      product: order.product,
    })
    // SUDARSHAN: the quote below is useLiveQuote's, live while the dialog is open.
    setModifyDialogOpen(true)
  }

  const handleModifyOrder = async () => {
    if (!modifyingOrder) return

    const pt = modifyForm.pricetype
    const sendsPrice = pt === 'LIMIT' || pt === 'SL'
    const sendsTrigger = pt === 'SL' || pt === 'SL-M'

    try {
      const response = await tradingApi.modifyOrder(modifyingOrder.orderid, {
        symbol: modifyingOrder.symbol,
        exchange: modifyingOrder.exchange,
        action: modifyingOrder.action,
        product: modifyingOrder.product,
        pricetype: pt,
        quantity: modifyForm.quantity,
        ...(sendsPrice && { price: modifyForm.price }),
        ...(sendsTrigger && { trigger_price: modifyForm.trigger_price }),
      })
      if (response.status === 'success') {
        showToast.success(`Order modified: ${modifyingOrder.orderid}`, 'orders')
        setModifyDialogOpen(false)
        setTimeout(() => fetchOrders(true), 1000)
      } else {
        showToast.error(response.message || 'Failed to modify order', 'orders')
      }
    } catch (error) {
      const axiosError = error as { response?: { data?: { message?: string } } }
      const message = axiosError.response?.data?.message || 'Failed to modify order'
      showToast.error(message, 'orders')
    }
  }

  const exportToCSV = () => {
    if (sortedAndFilteredOrders.length === 0) {
      showToast.error('No data to export', 'system')
      return
    }

    try {
      const headers = [
        'Symbol',
        'Exchange',
        'Action',
        'Qty',
        'Price',
        'Trigger',
        'Type',
        ...(isCrypto ? [] : ['Product']),
        'Order ID',
        'Status',
        'Time',
      ]
      const rows = sortedAndFilteredOrders.map((o) => [
        sanitizeCSV(o.symbol),
        sanitizeCSV(o.exchange),
        sanitizeCSV(o.action),
        sanitizeCSV(o.quantity),
        sanitizeCSV(o.price),
        sanitizeCSV(o.trigger_price),
        sanitizeCSV(o.pricetype),
        ...(isCrypto ? [] : [sanitizeCSV(o.product)]),
        sanitizeCSV(o.orderid),
        sanitizeCSV(o.order_status),
        sanitizeCSV(o.timestamp),
      ])

      const csv = [headers, ...rows].map((row) => row.join(',')).join('\n')
      const blob = new Blob([csv], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const filename = `orderbook_${new Date().toISOString().split('T')[0]}.csv`
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      showToast.success(`Downloaded ${filename}`, 'clipboard')
    } catch {
      showToast.error('Failed to export CSV', 'system')
    }
  }

  const openOrders = orders.filter((o) => o.order_status === 'open')

  const FilterChip = ({ status, label }: { status: string; label: string }) => (
    <Button
      variant={statusFilter.includes(status) ? 'default' : 'outline'}
      size="sm"
      className="rounded-full"
      onClick={() => toggleStatusFilter(status)}
    >
      {label}
    </Button>
  )

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Order Book</h1>
        <p className="text-muted-foreground">View and manage your orders</p>
      </div>

      <Tabs defaultValue="orders" className="space-y-6">
        <TabsList className="h-10">
          <TabsTrigger value="orders" className="min-w-[110px] text-sm">
            Orders
          </TabsTrigger>
          <TabsTrigger value="gtt" className="min-w-[110px] text-sm">
            GTT
          </TabsTrigger>
        </TabsList>

        {/* SUDARSHAN M6.9: on a phone the Orders tab holds a card per order. The wide
            toolbar, stats and table below then take a tab value that never opens, so
            they are not mounted; the GTT tab and the modify dialog are the same on both. */}
        {phone && (
          <TabsContent value="orders">
            <OrderCards
              orders={orders}
              loading={isLoading}
              error={error}
              refreshing={isRefreshing}
              apiKey={apiKey}
              marketOpen={marketOpen}
              onRefresh={fetchOrders}
              onCancel={handleCancelOrder}
              onCancelAll={handleCancelAllOrders}
              onModify={openModifyDialog}
              modifyOpen={modifyDialogOpen}
              onModifyOpenChange={setModifyDialogOpen}
            />
          </TabsContent>
        )}
        <TabsContent value={phone ? 'orders-wide' : 'orders'} className="space-y-6">
          {/* Orders tab toolbar */}
          <div className="flex items-center justify-start sm:justify-end gap-2 flex-wrap">
            {/* SUDARSHAN: where the LTP of the working orders stands. */}
            {working.length > 0 && (
              <span className="flex min-h-9 items-center gap-1.5 sm:mr-auto">
                <span className="text-xs font-semibold text-muted-foreground">LTP</span>
                <LiveLine line={ltpLine} now={ltpNow} />
              </span>
            )}
            {/* Settings Button */}
            <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
              <DialogTrigger asChild>
                <Button
                  variant={hasActiveFilters ? 'default' : 'outline'}
                  size="sm"
                  className="relative"
                >
                  <Settings2 className="h-4 w-4 mr-2" />
                  Filters
                  {hasActiveFilters && (
                    <span className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-[color:var(--gold)] rounded-full" />
                  )}
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-md">
                <DialogHeader>
                  <DialogTitle>Order Filters</DialogTitle>
                  <DialogDescription>Filter orders by status</DialogDescription>
                </DialogHeader>

                <div className="space-y-6 py-4">
                  {/* Order Status */}
                  <div className="space-y-3">
                    <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Order Status
                    </Label>
                    <div className="flex flex-wrap gap-2">
                      <FilterChip status="complete" label="Complete" />
                      <FilterChip status="open" label="Open" />
                      <FilterChip status="rejected" label="Rejected" />
                      <FilterChip status="cancelled" label="Cancelled" />
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
              onClick={() => fetchOrders(true)}
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
                <Button variant="destructive" size="sm" disabled={openOrders.length === 0}>
                  <X className="h-4 w-4 mr-2" />
                  Cancel All
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Cancel All Orders?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This will cancel all {openOrders.length} open orders. This action cannot be
                    undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Keep Orders</AlertDialogCancel>
                  <AlertDialogAction onClick={handleCancelAllOrders}>Cancel All</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>

          {/* Active Filters Bar */}
          {hasActiveFilters && (
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm text-muted-foreground">Active Filters:</span>
              {statusFilter.map((status) => (
                <Badge
                  key={status}
                  variant="secondary"
                  className="border-transparent bg-[color:var(--primary-tint)] text-primary"
                >
                  {status}
                </Badge>
              ))}
              <Button
                variant="outline"
                size="sm"
                className="text-destructive border-destructive/50 hover:bg-destructive/10"
                onClick={clearFilters}
              >
                Clear All
              </Button>
            </div>
          )}

          {/* Stats Cards */}
          <div className="grid grid-cols-3 gap-2 sm:gap-4 md:grid-cols-5">
            <Card className={STAT_CARD}>
              <CardHeader className={STAT_HEADER}>
                <CardDescription className={STAT_LABEL}>Buy Orders</CardDescription>
                <CardTitle className={cn(STAT_VALUE, 'text-[color:var(--buy-solid)]')}>
                  {stats?.total_buy_orders ?? 0}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card className={STAT_CARD}>
              <CardHeader className={STAT_HEADER}>
                <CardDescription className={STAT_LABEL}>Sell Orders</CardDescription>
                <CardTitle className={cn(STAT_VALUE, 'text-[color:var(--sell-solid)]')}>
                  {stats?.total_sell_orders ?? 0}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card className={STAT_CARD}>
              <CardHeader className={STAT_HEADER}>
                <CardDescription className={STAT_LABEL}>Completed</CardDescription>
                <CardTitle className={STAT_VALUE}>{stats?.total_completed_orders ?? 0}</CardTitle>
              </CardHeader>
            </Card>
            <Card className={STAT_CARD}>
              <CardHeader className={STAT_HEADER}>
                <CardDescription className={STAT_LABEL}>Open</CardDescription>
                <CardTitle className={cn(STAT_VALUE, 'text-[color:var(--warning-text)]')}>
                  {stats?.total_open_orders ?? 0}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card className={STAT_CARD}>
              <CardHeader className={STAT_HEADER}>
                <CardDescription className={STAT_LABEL}>Rejected</CardDescription>
                <CardTitle className={cn(STAT_VALUE, 'text-[color:var(--down)]')}>
                  {stats?.total_rejected_orders ?? 0}
                </CardTitle>
              </CardHeader>
            </Card>
          </div>

          {/* Orders Table */}
          <Card>
            <CardContent className="relative px-3 py-0 sm:px-6">
              {isLoading ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="h-8 w-8 animate-spin" />
                </div>
              ) : error ? (
                <div className="text-center py-12 text-muted-foreground">{error}</div>
              ) : sortedAndFilteredOrders.length === 0 ? (
                hasActiveFilters ? (
                  <EmptyState
                    icon={FilterX}
                    title="No orders match your filters"
                    description="Try adjusting or clearing your filters to see results."
                    action={
                      <Button variant="ghost" size="sm" onClick={clearFilters}>
                        Clear Filters
                      </Button>
                    }
                  />
                ) : (
                  <EmptyState
                    icon={ClipboardList}
                    title="No orders today"
                    description="Orders you place will display here."
                  />
                )
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead
                          className="w-[120px] cursor-pointer hover:bg-muted/50 transition-colors"
                          onClick={() => requestSort('symbol')}
                        >
                          <div className="flex items-center gap-1">
                            Symbol
                            {sortConfig.key === 'symbol' &&
                              (sortConfig.direction === 'asc' ? (
                                <ArrowUp className="h-3 w-3" />
                              ) : (
                                <ArrowDown className="h-3 w-3" />
                              ))}
                          </div>
                        </TableHead>
                        <TableHead className="w-[80px]">Exchange</TableHead>
                        <TableHead
                          className="w-[70px] cursor-pointer hover:bg-muted/50 transition-colors"
                          onClick={() => requestSort('action')}
                        >
                          <div className="flex items-center gap-1">
                            Action
                            {sortConfig.key === 'action' &&
                              (sortConfig.direction === 'asc' ? (
                                <ArrowUp className="h-3 w-3" />
                              ) : (
                                <ArrowDown className="h-3 w-3" />
                              ))}
                          </div>
                        </TableHead>
                        <TableHead className="w-[70px] text-right">Qty</TableHead>
                        <TableHead
                          className="w-[100px] text-right cursor-pointer hover:bg-muted/50 transition-colors"
                          onClick={() => requestSort('price')}
                        >
                          <div className="flex items-center justify-end gap-1">
                            Price
                            {sortConfig.key === 'price' &&
                              (sortConfig.direction === 'asc' ? (
                                <ArrowUp className="h-3 w-3" />
                              ) : (
                                <ArrowDown className="h-3 w-3" />
                              ))}
                          </div>
                        </TableHead>
                        <TableHead className="w-[100px] text-right">Trigger</TableHead>
                        {/* SUDARSHAN: the live price beside a working order. */}
                        <TableHead className="w-[100px] text-right">LTP</TableHead>
                        <TableHead className="w-[80px]">Type</TableHead>
                        {!isCrypto && <TableHead className="w-[70px]">Product</TableHead>}
                        <TableHead className="w-[140px]">Order ID</TableHead>
                        <TableHead
                          className="w-[100px] cursor-pointer hover:bg-muted/50 transition-colors"
                          onClick={() => requestSort('order_status')}
                        >
                          <div className="flex items-center gap-1">
                            Status
                            {sortConfig.key === 'order_status' &&
                              (sortConfig.direction === 'asc' ? (
                                <ArrowUp className="h-3 w-3" />
                              ) : (
                                <ArrowDown className="h-3 w-3" />
                              ))}
                          </div>
                        </TableHead>
                        <TableHead
                          className="w-[100px] cursor-pointer hover:bg-muted/50 transition-colors"
                          onClick={() => requestSort('timestamp')}
                        >
                          <div className="flex items-center gap-1">
                            Time
                            {sortConfig.key === 'timestamp' &&
                              (sortConfig.direction === 'asc' ? (
                                <ArrowUp className="h-3 w-3" />
                              ) : (
                                <ArrowDown className="h-3 w-3" />
                              ))}
                          </div>
                        </TableHead>
                        <TableHead className="w-[60px]">Cancel</TableHead>
                        <TableHead className="w-[60px]">Modify</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {sortedAndFilteredOrders.map((order, index) => {
                        const status = statusConfig[order.order_status] || statusConfig.open
                        const StatusIcon = status.icon
                        const canCancel = order.order_status === 'open'

                        return (
                          <TableRow key={`${order.orderid}-${index}`}>
                            <TableCell className="font-medium">
                              <BookSymbolLink symbol={order.symbol} exchange={order.exchange} />
                            </TableCell>
                            <TableCell>
                              <Badge variant="outline">{order.exchange}</Badge>
                            </TableCell>
                            <TableCell>
                              <Badge className={SIDE_BADGE[order.action]}>{order.action}</Badge>
                            </TableCell>
                            <TableCell className="text-right font-mono">{order.quantity}</TableCell>
                            <TableCell className="text-right font-mono">
                              {formatCurrency(order.price)}
                            </TableCell>
                            <TableCell className="text-right font-mono">
                              {formatCurrency(order.trigger_price)}
                            </TableCell>
                            <TableCell className="text-right font-mono">
                              {/* SUDARSHAN: working orders only; a final one has no price to watch. */}
                              {(() => {
                                const live = isFinalStatus(order.order_status)
                                  ? undefined
                                  : priceOf.get(`${order.exchange}:${order.symbol}`)
                                return live ? (
                                  <TickPrice
                                    value={live.ltp}
                                    direction={live._tick}
                                    stale={ltpStale(live)}
                                    format={formatCurrency}
                                  />
                                ) : (
                                  <span className="text-muted-foreground">-</span>
                                )
                              })()}
                            </TableCell>
                            <TableCell>
                              <Badge variant="secondary">{order.pricetype}</Badge>
                            </TableCell>
                            {!isCrypto && (
                              <TableCell>
                                <Badge variant="outline">{order.product}</Badge>
                              </TableCell>
                            )}
                            <TableCell className="font-mono text-xs">{order.orderid}</TableCell>
                            <TableCell>
                              <div className={cn('flex items-center gap-1', status.color)}>
                                <StatusIcon className="h-4 w-4" />
                                <span className="text-sm">{status.label}</span>
                              </div>
                            </TableCell>
                            <TableCell className="text-sm text-muted-foreground">
                              {formatTime(order.timestamp)}
                            </TableCell>
                            <TableCell>
                              {canCancel && (
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10"
                                  onClick={() => handleCancelOrder(order.orderid)}
                                  aria-label={`Cancel order ${order.orderid}`}
                                >
                                  <X className="h-4 w-4" />
                                </Button>
                              )}
                            </TableCell>
                            <TableCell>
                              {canCancel && (
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-8 w-8 text-primary hover:text-primary/80"
                                  onClick={() => openModifyDialog(order)}
                                  aria-label={`Modify order for ${order.symbol}`}
                                >
                                  <Pencil className="h-4 w-4" />
                                </Button>
                              )}
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
              {/* SUDARSHAN: on a phone the table scrolls sideways; the fade says so. */}
              {!isLoading && !error && sortedAndFilteredOrders.length > 0 && (
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-y-0 right-3 w-10 bg-gradient-to-l from-card to-transparent sm:right-6 md:hidden"
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="gtt" className="space-y-6">
          <GttTab />
        </TabsContent>
      </Tabs>

      {/* Modify Order Dialog */}
      <Dialog open={modifyDialogOpen} onOpenChange={setModifyDialogOpen}>
        <DialogContent className="sm:max-w-[550px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-3">
              <span>Modify Order</span>
              <Badge className={SIDE_BADGE[modifyingOrder?.action ?? '']}>
                {modifyingOrder?.action}
              </Badge>
            </DialogTitle>
            <DialogDescription className="sr-only">Modify order details</DialogDescription>
          </DialogHeader>

          {/* Symbol and Order Info */}
          <div className="rounded-lg border p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-lg font-semibold">{modifyingOrder?.symbol}</div>
                <div className="text-sm text-muted-foreground">{modifyingOrder?.exchange}</div>
              </div>
              <div className="text-right">
                <div className="text-xs text-muted-foreground">Order ID</div>
                <div className="font-mono text-sm">{modifyingOrder?.orderid}</div>
              </div>
            </div>
            <div className="flex items-center gap-3 pt-2 border-t">
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Type:</span>
                <Badge variant="secondary" className="text-xs">
                  {modifyForm.pricetype}
                </Badge>
              </div>
              {!isCrypto && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Product:</span>
                  <Badge variant="outline" className="text-xs">
                    {modifyForm.product}
                  </Badge>
                </div>
              )}
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Qty:</span>
                <span className="font-mono text-sm font-medium">{modifyForm.quantity}</span>
              </div>
            </div>
          </div>

          {/* Market Quotes Section */}
          <div className="rounded-lg border bg-muted/50 p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium">Market Data</span>
              {/* SUDARSHAN: live while the dialog is open, with its status. */}
              {isLoadingQuotes ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                quotes &&
                modifyingOrder && (
                  <QuoteStatus
                    exchange={modifyingOrder.exchange}
                    feed={quoteFeed}
                    calendar={quoteCalendar}
                  />
                )
              )}
            </div>
            {quotes ? (
              <div className="grid grid-cols-4 gap-4 text-sm">
                <div>
                  <div className="text-muted-foreground text-xs">LTP</div>
                  <div className="font-mono font-semibold">{quoteFigure(quotes.ltp)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground text-xs">Bid</div>
                  <div className="font-mono text-[color:var(--up)]">
                    {quoteFigure(quotes.bidPrice)}
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground text-xs">Ask</div>
                  <div className="font-mono text-[color:var(--down)]">
                    {quoteFigure(quotes.askPrice)}
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground text-xs">Prev Close</div>
                  <div className="font-mono">{quoteFigure(quotes.close)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground text-xs">Open</div>
                  <div className="font-mono">{quoteFigure(quotes.open)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground text-xs">High</div>
                  <div className="font-mono text-[color:var(--up)]">{quoteFigure(quotes.high)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground text-xs">Low</div>
                  <div className="font-mono text-[color:var(--down)]">
                    {quoteFigure(quotes.low)}
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground text-xs">Volume</div>
                  <div className="font-mono">
                    {quotes.volume === undefined ? '-' : quotes.volume.toLocaleString('en-IN')}
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-sm text-muted-foreground">
                {isLoadingQuotes ? 'Loading quotes...' : 'Quotes not available'}
              </div>
            )}
          </div>

          {/* Editable Fields - Based on Order Type */}
          <div className="grid gap-4 py-2">
            {/* Quantity field - shown for all modifiable order types (LIMIT, SL, SL-M) */}
            {modifyForm.pricetype !== 'MARKET' && (
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="quantity" className="text-right">
                  Quantity
                </Label>
                <Input
                  id="quantity"
                  type="number"
                  step={isCrypto ? 'any' : '1'}
                  min="0"
                  value={modifyForm.quantity}
                  onChange={(e) => {
                    const raw = e.target.value
                    const parsed = isCrypto ? parseFloat(raw) : parseInt(raw, 10)
                    setModifyForm({ ...modifyForm, quantity: Number.isFinite(parsed) ? parsed : 0 })
                  }}
                  className="col-span-3"
                />
              </div>
            )}
            {/* Price field - shown for LIMIT and SL orders */}
            {(modifyForm.pricetype === 'LIMIT' || modifyForm.pricetype === 'SL') && (
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="price" className="text-right">
                  Price
                </Label>
                <Input
                  id="price"
                  type="number"
                  step="0.05"
                  value={modifyForm.price}
                  onChange={(e) =>
                    setModifyForm({ ...modifyForm, price: parseFloat(e.target.value) || 0 })
                  }
                  className="col-span-3"
                />
              </div>
            )}
            {/* Trigger Price field - shown for SL and SL-M orders */}
            {(modifyForm.pricetype === 'SL' || modifyForm.pricetype === 'SL-M') && (
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="trigger_price" className="text-right">
                  Trigger Price
                </Label>
                <Input
                  id="trigger_price"
                  type="number"
                  step="0.05"
                  value={modifyForm.trigger_price}
                  onChange={(e) =>
                    setModifyForm({ ...modifyForm, trigger_price: parseFloat(e.target.value) || 0 })
                  }
                  className="col-span-3"
                />
              </div>
            )}
            {/* Info for MARKET orders */}
            {modifyForm.pricetype === 'MARKET' && (
              <div className="text-sm text-muted-foreground text-center py-2">
                Market orders cannot be modified. Cancel and place a new order.
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModifyDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleModifyOrder}>Modify Order</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
