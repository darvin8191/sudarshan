// components/trading/PlaceOrderDialog.tsx
// Reusable order placement dialog with real-time quotes and market depth
// Uses WebSocket for real-time data with REST API fallback (like Holdings/Positions)

import { X } from 'lucide-react'
import { useCallback, useEffect, useId, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useLiveQuote } from '@/hooks/useLiveQuote'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
// SUDARSHAN: where the quote stands (Real-time, Reconnecting, Updated, Market closed).
import { QuoteStatus } from '@/sudarshan/books/QuoteStatus'
// SUDARSHAN M20.7: the kill switch's line on the ticket.
import { KillSwitchNote } from '@/sudarshan/risk/RiskBanner'
// SUDARSHAN B5 (M5.9): the margin the order needs and the funds available; it holds nothing back.
import { MarginLine } from '@/sudarshan/ticket/MarginLine'
import { SettlementLine } from '@/sudarshan/ticket/SettlementLine'
// SUDARSHAN B4 (M5.11): size by risk, which fills the quantity box and sends nothing.
import { SizeByRisk } from '@/sudarshan/ticket/SizeByRisk'
import { entryOf } from '@/sudarshan/ticket/sizeHelper'
// SUDARSHAN M6.9: a ticket that has just opened takes no tap for half a second.
import { useOpenGuard } from '@/sudarshan/watchlist/openGuard'
import { showToast } from '@/utils/toast'
import { MarketDepthPanel } from './MarketDepthPanel'
import { QuoteHeader } from './QuoteHeader'

// Price types for order dialog
// Backend API accepts: MARKET, LIMIT, SL (Stop Loss Limit), SL-M (Stop Loss Market)
const PRICE_TYPES = [
  { value: 'MARKET', label: 'Market' },
  { value: 'LIMIT', label: 'Limit' },
  { value: 'SL-M', label: 'SL-M' }, // Stop Loss Market (trigger only)
  { value: 'SL', label: 'SL-L' }, // Stop Loss Limit (trigger + price)
] as const

// Product types based on exchange
const FNO_PRODUCT_TYPES = [
  { value: 'NRML', label: 'NRML' },
  { value: 'MIS', label: 'MIS' },
] as const

const EQUITY_PRODUCT_TYPES = [
  { value: 'CNC', label: 'CNC' },
  { value: 'MIS', label: 'MIS' },
] as const

export interface PlaceOrderDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol?: string
  exchange?: string
  action?: 'BUY' | 'SELL'
  quantity?: number
  lotSize?: number
  tickSize?: number
  product?: 'MIS' | 'NRML' | 'CNC'
  priceType?: 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
  /**
   * Starting limit and trigger prices, for a caller that already has one: a
   * chart row clicked at a price. Left out, a LIMIT or SL ticket fills its
   * price from the live LTP as before.
   */
  price?: number
  triggerPrice?: number
  strategy?: string
  /**
   * Where to send the confirmed order. Left out, it posts through
   * tradingApi.placeOrder as it always has. The chart supplies one so its
   * ticket goes through the terminal's trade feed, which asserts the page's
   * mode against the server before anything is sent; a ticket must not be
   * the one order route that decides on the server's global switch.
   */
  place?: (order: TicketOrder) => Promise<{ orderId: string }>
  /**
   * The element the dialog is portalled into, for a caller that is the
   * fullscreen element: nothing outside it paints, so a dialog on body would
   * open unseen and take focus. Null or absent means body, as before.
   */
  container?: HTMLElement | null
  onSuccess?: (orderId: string) => void
  onError?: (error: string) => void
}

/** The confirmed order, in the shape the placeorder endpoint takes. */
export interface TicketOrder {
  symbol: string
  exchange: string
  action: 'BUY' | 'SELL'
  quantity: number
  pricetype: 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
  product: 'MIS' | 'NRML' | 'CNC'
  price?: number
  trigger_price?: number
}

/**
 * The lot count a starting quantity stands for. The box shows lots on a
 * derivative segment while the order carries units, so the two are derived
 * from one figure here rather than seeded separately: a 150-unit ticket on a
 * 75-lot contract reads 2, not 1.
 */
export function lotsFor(quantity: number | undefined, lotSize: number): number {
  if (quantity === undefined || lotSize <= 0) return 1
  return Math.max(1, Math.round(quantity / lotSize))
}

// Tick size validation helpers
function roundToTick(price: number, tickSize: number): number {
  if (tickSize <= 0) return price
  // Use toFixed to avoid floating point precision issues (e.g., 140.95000000000002)
  return Number((Math.round(price / tickSize) * tickSize).toFixed(2))
}

function adjustPrice(price: number, tickSize: number, direction: 'up' | 'down'): number {
  const rounded = roundToTick(price, tickSize)
  if (direction === 'up') {
    return Number((rounded + tickSize).toFixed(2))
  }
  return Math.max(0, Number((rounded - tickSize).toFixed(2)))
}

// Check if exchange is F&O/Commodity/Currency (uses NRML/MIS)
// NSE, BSE = Equity → CNC/MIS
// NFO, BFO, CDS, BCD, MCX, NCDEX, NCO = F&O/Currency/Commodity → NRML/MIS
// CRYPTO = crypto derivatives (utils/constants.py): NRML/MIS in lots, no CNC
function isFnOExchange(exchange: string): boolean {
  return ['NFO', 'BFO', 'MCX', 'CDS', 'BCD', 'NCDEX', 'NCO', 'CRYPTO'].includes(exchange)
}

// SUDARSHAN (v0.13.0 review M2): the ticket at phone size and in the D tokens. Presentation
// only: nothing here changes what the ticket sends.
const SIDE_FILL = {
  BUY: 'bg-[color:var(--buy-solid)] text-white hover:bg-[color:var(--buy-solid)] hover:opacity-90',
  SELL: 'bg-[color:var(--sell-solid)] text-white hover:bg-[color:var(--sell-solid)] hover:opacity-90',
} as const
const SIDE_INK = {
  BUY: 'text-[color:var(--buy-solid)]',
  SELL: 'text-[color:var(--sell-solid)]',
} as const
/** A control a thumb can hit: 48 dp. */
const TOUCH = 'h-12 min-w-12'
const STEP = 'size-12 shrink-0 px-0 text-lg'
const UNITS = new Intl.NumberFormat('en-IN')

/**
 * The order as one line, shown beside the Place button (v0.13.0 review H1): on a phone the body
 * scrolls, so the price can be off screen when the button is pressed. The line never is.
 * "SELL 1 SBIN · LIMIT 803.00 · CNC"; a stop adds its trigger; lots are said with the units.
 */
export function orderLine(order: {
  action: 'BUY' | 'SELL'
  quantity: number
  lots?: number
  symbol: string
  priceType: 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
  price: number
  trigger: number
  product: string
}): { side: string; rest: string } {
  const type = PRICE_TYPES.find((entry) => entry.value === order.priceType)?.label ?? ''
  const lots =
    order.lots === undefined ? '' : ` (${order.lots} ${order.lots === 1 ? 'lot' : 'lots'})`
  const price =
    order.priceType === 'LIMIT' || order.priceType === 'SL' ? ` ${order.price.toFixed(2)}` : ''
  const trigger =
    order.priceType === 'SL' || order.priceType === 'SL-M'
      ? `, trigger ${order.trigger.toFixed(2)}`
      : ''
  return {
    side: order.action,
    rest: `${UNITS.format(order.quantity)}${lots} ${order.symbol} · ${type.toUpperCase()}${price}${trigger} · ${order.product}`,
  }
}

export function PlaceOrderDialog({
  open,
  onOpenChange,
  symbol = '',
  exchange = '',
  action: initialAction = 'BUY',
  quantity: initialQuantity,
  lotSize = 1,
  tickSize = 0.05,
  product: initialProduct = 'NRML',
  priceType: initialPriceType = 'MARKET',
  price: initialPrice,
  triggerPrice: initialTriggerPrice,
  strategy = 'OptionChain',
  place,
  container,
  onSuccess,
  onError,
}: PlaceOrderDialogProps) {
  const { apiKey } = useAuthStore()
  // SUDARSHAN B5: Analyze mode's funds are the sandbox's; the margin line says so.
  const sandbox = useThemeStore((state) => state.appMode) === 'analyzer'
  // SUDARSHAN M6.9: the second tap of a double tap on the Add, Buy more or Sell
  // that opened the ticket lands on it (on SELL, at 412x915, after "Buy more").
  const guard = useOpenGuard(open)

  // Form state
  // SUDARSHAN (SCR-P item 7): each number box is its label's, so it has a name to be found and read by.
  const fieldId = useId()
  const [formAction, setFormAction] = useState<'BUY' | 'SELL'>(initialAction)
  const [formQuantity, setFormQuantity] = useState(initialQuantity ?? lotSize)
  const [formPriceType, setFormPriceType] = useState(initialPriceType)
  const [formProduct, setFormProduct] = useState(initialProduct)
  const [formPrice, setFormPrice] = useState(0)
  const [formTriggerPrice, setFormTriggerPrice] = useState(0)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isDepthExpanded, setIsDepthExpanded] = useState(false)
  const [quantityMode, setQuantityMode] = useState<'lots' | 'shares'>('lots')
  const [lotMultiplier, setLotMultiplier] = useState(1)

  // Equity trades on either cash exchange. The caller supplies the starting
  // exchange; for NSE/BSE the user may switch, since the same scrip is listed
  // on both. Everything else is contract-specific and stays fixed.
  const [formExchange, setFormExchange] = useState(exchange)
  const exchange_ = formExchange || exchange
  const isEquityExchange = exchange_ === 'NSE' || exchange_ === 'BSE'

  // Get available product types based on exchange
  const productTypes = isFnOExchange(exchange_) ? FNO_PRODUCT_TYPES : EQUITY_PRODUCT_TYPES

  // Lots are a derivatives concept. Cash equity has no lot size worth showing.
  const usesLots = isFnOExchange(exchange_) && lotSize > 1

  // Centralized live quote + depth with REST fallback (like useLivePrice for Holdings/Positions)
  // SUDARSHAN: one REST snapshot when the ticket opens, then ticks; no polling while open.
  const {
    data: liveData,
    isLoading: isLoadingQuotes,
    isConnected,
    feed: quoteFeed,
    calendar: quoteCalendar,
  } = useLiveQuote(symbol, exchange_, {
    enabled: open && !!symbol && !!exchange_,
    mode: 'Depth',
    useQuotesFallback: true,
    useDepthFallback: true,
  })

  // Reset form when dialog opens with new values
  useEffect(() => {
    if (open) {
      setFormAction(initialAction)
      setFormQuantity(initialQuantity ?? lotSize)
      setFormPriceType(initialPriceType)
      // Set default product based on exchange, validate initialProduct is valid for exchange
      const isFnO = isFnOExchange(exchange)
      const defaultProduct = isFnO ? 'NRML' : 'CNC'
      // Validate product: CNC not valid for F&O, NRML not valid for equity
      const validProducts = isFnO ? ['NRML', 'MIS'] : ['CNC', 'MIS']
      const productToUse =
        initialProduct && validProducts.includes(initialProduct) ? initialProduct : defaultProduct
      setFormProduct(productToUse)
      // A supplied price survives the LTP fill below, which only writes over 0.
      setFormPrice(initialPrice ?? 0)
      setFormTriggerPrice(initialTriggerPrice ?? 0)
      setIsDepthExpanded(false)
      // Cash equity has no lots, so the quantity box must show the real share
      // count. Defaulting to 'lots' here displayed lotMultiplier (1) while
      // formQuantity held the actual size -- opening Exit on a 70-share holding
      // showed "1" in the box and would have placed 70.
      setQuantityMode(isFnOExchange(exchange) && lotSize > 1 ? 'lots' : 'shares')
      // Derived from the same figure as formQuantity above, so the Lots box
      // and the order it places agree: the chart's ticket arrives in units
      // for however many lots its pane asked for, and a box reading 1 over
      // an order of 150 is the mistake the ticket exists to prevent.
      setLotMultiplier(lotsFor(initialQuantity, lotSize))
      setFormExchange(exchange)
    }
  }, [
    open,
    initialAction,
    initialQuantity,
    lotSize,
    initialPriceType,
    initialProduct,
    initialPrice,
    initialTriggerPrice,
    exchange,
  ])

  // Use data from centralized hook
  const mergedData = {
    ltp: liveData.ltp,
    close: liveData.close,
    change: liveData.change,
    change_percent: liveData.changePercent,
    bidPrice: liveData.bidPrice,
    askPrice: liveData.askPrice,
    bidSize: liveData.bidSize,
    askSize: liveData.askSize,
    depth: liveData.depth,
  }

  const displayChange = mergedData.change
  const displayChangePercent = mergedData.change_percent

  // Set price to LTP when switching to LIMIT and LTP is available
  useEffect(() => {
    if (formPriceType !== 'MARKET' && mergedData.ltp && formPrice === 0) {
      // SUDARSHAN (v0.13.0 phone fix): decided on the price as it stands, not as this render saw
      // it. A ticket that opens with a price ("Buy at 803.00") while the last price is already
      // known ran this in the same pass as the reset above, and the last price replaced the
      // price that was asked for.
      const ltp = mergedData.ltp
      setFormPrice((current) => (current === 0 ? roundToTick(ltp, tickSize) : current))
    }
  }, [formPriceType, mergedData.ltp, formPrice, tickSize])

  // Validation - determine which price fields are needed:
  // LIMIT: price only
  // SL-M (Stop Loss Market): trigger price only
  // SL (Stop Loss Limit): both price and trigger price
  const needsPrice = formPriceType === 'LIMIT' || formPriceType === 'SL'
  const needsTrigger = formPriceType === 'SL-M' || formPriceType === 'SL'

  const isValid = useCallback(() => {
    if (!symbol || !exchange_) return false
    if (!apiKey) return false
    if (formQuantity <= 0) return false
    if (needsPrice && formPrice <= 0) return false
    if (needsTrigger && formTriggerPrice <= 0) return false
    return true
  }, [
    symbol,
    exchange_,
    apiKey,
    formQuantity,
    needsPrice,
    formPrice,
    needsTrigger,
    formTriggerPrice,
  ])

  // Submit order
  const handleSubmit = async () => {
    if (!isValid()) {
      showToast.error('Please fill all required fields')
      return
    }

    if (!apiKey) {
      showToast.error('API key not found. Please set up your API key.')
      onError?.('API key not found')
      return
    }

    setIsSubmitting(true)
    try {
      // Price types are now directly mapped to API values
      // Backend accepts: MARKET, LIMIT, SL (Stop Loss Limit), SL-M (Stop Loss Market)
      const apiPriceType = formPriceType as 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'

      const order: TicketOrder = {
        exchange: exchange_,
        symbol,
        action: formAction,
        quantity: formQuantity,
        pricetype: apiPriceType,
        product: formProduct,
        ...(needsPrice && { price: formPrice }),
        ...(needsTrigger && { trigger_price: formTriggerPrice }),
      }

      // A caller's own route (the chart's trade feed, which checks the mode
      // before it posts) takes the order whole and reports by throwing.
      if (place) {
        const placed = await place(order)
        onSuccess?.(placed.orderId)
        onOpenChange(false)
        return
      }

      const response = await tradingApi.placeOrder({ apikey: apiKey, strategy, ...order })

      // Response structure: { status: "success", orderid: "..." } or { status: "error", message: "..." }
      // Note: orderid is at root level, not in data field
      const orderid = (response as unknown as { orderid?: string }).orderid
      if (response.status === 'success' && orderid) {
        // Toast is shown by useSocket when WebSocket receives order update
        onSuccess?.(orderid)
        onOpenChange(false)
      } else {
        const errorMsg = response.message || 'Order placement failed'
        showToast.error(errorMsg, 'orders')
        onError?.(errorMsg)
      }
    } catch (err: unknown) {
      // Extract error message from axios error response or fallback to error message
      let errorMsg = 'Order placement failed'
      if (err && typeof err === 'object') {
        const axiosError = err as { response?: { data?: { message?: string } }; message?: string }
        if (axiosError.response?.data?.message) {
          errorMsg = axiosError.response.data.message
        } else if (axiosError.message) {
          errorMsg = axiosError.message
        }
      }
      showToast.error(errorMsg, 'orders')
      onError?.(errorMsg)
    } finally {
      setIsSubmitting(false)
    }
  }

  // Quantity change handler
  const handleQuantityChange = (value: string) => {
    const num = parseInt(value, 10) || 0
    if (quantityMode === 'lots') {
      // In lots mode, multiply by lot size
      setFormQuantity(num * lotSize)
      setLotMultiplier(num)
    } else {
      // In shares mode, ensure it's a multiple of lot size
      const roundedQty = Math.max(lotSize, Math.round(num / lotSize) * lotSize)
      setFormQuantity(roundedQty)
      setLotMultiplier(roundedQty / lotSize)
    }
  }

  // Get display quantity based on mode
  const displayQuantity = quantityMode === 'lots' ? lotMultiplier : formQuantity

  // SUDARSHAN B4: the size the risk allows, in units, into the quantity box.
  const fillQuantity = (units: number) => {
    setFormQuantity(units)
    setLotMultiplier(Math.max(1, Math.round(units / lotSize)))
  }

  // Determine if we're still loading initial data
  const isLoading = isLoadingQuotes && !mergedData.ltp && !isConnected

  const line = orderLine({
    action: formAction,
    quantity: formQuantity,
    lots: usesLots ? lotMultiplier : undefined,
    symbol,
    priceType: formPriceType,
    price: formPrice,
    trigger: formTriggerPrice,
    product: formProduct,
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        {...guard.handlers}
        data-settling={guard.settling || undefined}
        // SUDARSHAN: on a short window the body scrolls inside the viewport and the title and
        // the Place / Cancel buttons stay in reach (the dialog is a three-row grid).
        className="sm:max-w-[420px] max-h-[calc(100dvh-1rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-3 overflow-hidden p-4"
        aria-describedby={undefined}
        container={container}
        showCloseButton={false}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 pr-10 text-left">
            <span>Place Order -</span>
            <span className={SIDE_INK[formAction]}>{formAction}</span>
            <span className="text-muted-foreground font-normal text-sm truncate">{symbol}</span>
          </DialogTitle>
        </DialogHeader>
        {/* SUDARSHAN (review M2): the close button at 48 dp, over the title row's corner. */}
        <DialogClose
          aria-label="Close"
          className="absolute top-0 right-0 flex size-12 items-center justify-center rounded-md text-[color:var(--text-secondary)] outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="size-5" aria-hidden="true" />
        </DialogClose>

        <div data-testid="ticket-body" className="space-y-3 py-1 min-h-0 overflow-y-auto">
          {/* SUDARSHAN M20.7: the kill switch's line; it disables nothing (a Buy can close a short). */}
          <KillSwitchNote />
          {/* Quote Header - merged WebSocket + REST data */}
          <QuoteHeader
            exchange={exchange_}
            ltp={mergedData.ltp}
            prevClose={mergedData.close}
            change={displayChange}
            changePercent={displayChangePercent}
            bidPrice={mergedData.bidPrice}
            askPrice={mergedData.askPrice}
            bidSize={mergedData.bidSize}
            askSize={mergedData.askSize}
            isLoading={isLoading}
          />
          {/* SUDARSHAN: never old numbers that look current. */}
          {!isLoading && mergedData.ltp !== undefined && quoteFeed && quoteCalendar && (
            <QuoteStatus
              exchange={exchange_}
              symbol={symbol}
              feed={quoteFeed}
              calendar={quoteCalendar}
              className="-mt-2"
            />
          )}

          {/* SUDARSHAN (review H1): side, quantity, price type, product, price and trigger come
              first, in that order, so a phone held upright shows them without scrolling. Size by
              risk, the exchange and the depth follow. */}
          {/* Action Toggle */}
          <div className="space-y-1">
            <Label id={`${fieldId}-action`} className="text-xs">
              Action
            </Label>
            {/* biome-ignore lint/a11y/useSemanticElements: a pressed pair under its label, not a fieldset */}
            <div role="group" aria-labelledby={`${fieldId}-action`} className="flex gap-2">
              {(['BUY', 'SELL'] as const).map((side) => (
                <Button
                  key={side}
                  type="button"
                  variant={formAction === side ? 'default' : 'outline'}
                  aria-pressed={formAction === side}
                  className={cn(
                    'flex-1 font-bold tracking-wide',
                    TOUCH,
                    formAction === side && SIDE_FILL[side]
                  )}
                  onClick={() => setFormAction(side)}
                >
                  {side}
                </Button>
              ))}
            </div>
          </div>

          {/* Quantity with Mode Toggle */}
          <div className="space-y-1">
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor={`${fieldId}-quantity`} className="text-xs">
                Quantity
              </Label>
              {usesLots && (
                <div className="flex gap-3 text-xs text-muted-foreground">
                  <span>Lot size: {lotSize}</span>
                  <span>Total qty: {formQuantity}</span>
                </div>
              )}
            </div>
            <div className="flex gap-2">
              <Input
                id={`${fieldId}-quantity`}
                type="number"
                inputMode="numeric"
                value={displayQuantity}
                onChange={(e) => handleQuantityChange(e.target.value)}
                min={1}
                className="h-12 flex-1"
              />
              {/* Cash equity trades in shares - there is no lot. Showing a
                  Lots/Shares toggle and "Lot size: 1" on NSE/BSE is noise that
                  implies a concept the segment does not have. */}
              {usesLots && (
                // biome-ignore lint/a11y/useSemanticElements: a pressed pair, not a form fieldset
                <div role="group" aria-label="Quantity in" className="flex gap-1">
                  {(['lots', 'shares'] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      aria-pressed={quantityMode === mode}
                      onClick={() => setQuantityMode(mode)}
                      className={cn(
                        'rounded-md border px-3 text-sm font-medium',
                        TOUCH,
                        quantityMode === mode
                          ? 'border-primary bg-primary text-primary-foreground'
                          : 'border-input bg-transparent text-[color:var(--text-secondary)]'
                      )}
                    >
                      {mode === 'lots' ? 'Lots' : 'Shares'}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Price Type and Product in row */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label id={`${fieldId}-pricetype`} className="text-xs">
                Price Type
              </Label>
              <Select
                value={formPriceType}
                onValueChange={(v) => setFormPriceType(v as typeof formPriceType)}
              >
                <SelectTrigger
                  aria-labelledby={`${fieldId}-pricetype`}
                  className="data-[size=default]:h-12"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRICE_TYPES.map((pt) => (
                    <SelectItem key={pt.value} value={pt.value} className="min-h-12">
                      {pt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label id={`${fieldId}-product`} className="text-xs">
                Product
              </Label>
              <Select
                value={formProduct}
                onValueChange={(v) => setFormProduct(v as typeof formProduct)}
              >
                <SelectTrigger
                  aria-labelledby={`${fieldId}-product`}
                  className="data-[size=default]:h-12"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {productTypes.map((pt) => (
                    <SelectItem key={pt.value} value={pt.value} className="min-h-12">
                      {pt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Price Input (conditional) */}
          {needsPrice && (
            <div className="space-y-1">
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor={`${fieldId}-price`} className="text-xs">
                  Price
                </Label>
                <span className="text-xs text-muted-foreground">Tick size: {tickSize}</span>
              </div>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  className={STEP}
                  aria-label="Lower the price by one tick"
                  onClick={() => setFormPrice(adjustPrice(formPrice, tickSize, 'down'))}
                >
                  -
                </Button>
                <Input
                  id={`${fieldId}-price`}
                  type="number"
                  inputMode="decimal"
                  value={formPrice}
                  onChange={(e) => setFormPrice(parseFloat(e.target.value) || 0)}
                  onBlur={() => setFormPrice(roundToTick(formPrice, tickSize))}
                  className="h-12 flex-1 text-center"
                  step={tickSize}
                  min={0}
                />
                <Button
                  type="button"
                  variant="outline"
                  className={STEP}
                  aria-label="Raise the price by one tick"
                  onClick={() => setFormPrice(adjustPrice(formPrice, tickSize, 'up'))}
                >
                  +
                </Button>
              </div>
            </div>
          )}

          {/* Trigger Price Input (conditional) */}
          {needsTrigger && (
            <div className="space-y-1">
              <Label htmlFor={`${fieldId}-trigger`} className="text-xs">
                Trigger Price
              </Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  className={STEP}
                  aria-label="Lower the trigger price by one tick"
                  onClick={() =>
                    setFormTriggerPrice(adjustPrice(formTriggerPrice, tickSize, 'down'))
                  }
                >
                  -
                </Button>
                <Input
                  id={`${fieldId}-trigger`}
                  type="number"
                  inputMode="decimal"
                  value={formTriggerPrice}
                  onChange={(e) => setFormTriggerPrice(parseFloat(e.target.value) || 0)}
                  onBlur={() => setFormTriggerPrice(roundToTick(formTriggerPrice, tickSize))}
                  className="h-12 flex-1 text-center"
                  step={tickSize}
                  min={0}
                />
                <Button
                  type="button"
                  variant="outline"
                  className={STEP}
                  aria-label="Raise the trigger price by one tick"
                  onClick={() => setFormTriggerPrice(adjustPrice(formTriggerPrice, tickSize, 'up'))}
                >
                  +
                </Button>
              </div>
            </div>
          )}

          {/* SUDARSHAN B4 (M5.11): a risk and a stop give whole lots for the quantity box. */}
          <SizeByRisk
            exchange={exchange_}
            side={formAction}
            lotSize={usesLots ? lotSize : 1}
            entry={entryOf({
              priceType: formPriceType,
              price: formPrice,
              trigger: formTriggerPrice,
              ltp: mergedData.ltp,
            })}
            onFill={fillQuantity}
          />

          {/* Exchange, for cash equity only. The same scrip is listed on both
              NSE and BSE, so the user picks where to route. Derivatives are
              contract-specific and stay on the exchange they came from. */}
          {isEquityExchange && (
            <div className="space-y-1">
              <Label id={`${fieldId}-exchange`} className="text-xs">
                Exchange
              </Label>
              <Select value={exchange_} onValueChange={setFormExchange}>
                <SelectTrigger
                  aria-labelledby={`${fieldId}-exchange`}
                  className="data-[size=default]:h-12"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="NSE" className="min-h-12">
                    NSE
                  </SelectItem>
                  <SelectItem value="BSE" className="min-h-12">
                    BSE
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Market Depth Panel - only from WebSocket */}
          <MarketDepthPanel
            depth={mergedData.depth}
            isExpanded={isDepthExpanded}
            onToggle={() => setIsDepthExpanded(!isDepthExpanded)}
            maxLevels={5}
          />

          {/* SUDARSHAN B7 (M5.21): a stock contract in its expiry week is settled by delivery. */}
          <SettlementLine symbol={symbol} exchange={exchange_} />

          {/* SUDARSHAN B5 (M5.9): read on open and on change; it never holds the button back. */}
          <MarginLine
            apiKey={apiKey}
            sandbox={sandbox}
            order={{
              symbol,
              exchange: exchange_,
              action: formAction,
              quantity: formQuantity,
              pricetype: formPriceType,
              product: formProduct,
              price: formPrice,
              triggerPrice: formTriggerPrice,
            }}
          />
        </div>

        {/* SUDARSHAN (review H1): the order in one line beside its button, always on screen. */}
        <DialogFooter className="flex-col gap-2 sm:flex-row sm:items-center sm:gap-2">
          <p
            id={`${fieldId}-summary`}
            data-testid="ticket-summary"
            className="min-w-0 flex-1 text-left text-sm leading-snug font-medium text-[color:var(--text-primary)] [overflow-wrap:anywhere]"
          >
            <span className={cn('font-bold', SIDE_INK[formAction])}>{line.side}</span> {line.rest}
          </p>
          <div className="grid grid-cols-2 gap-2 sm:flex sm:shrink-0">
            <Button
              variant="outline"
              className={TOUCH}
              onClick={() => onOpenChange(false)}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={!isValid() || isSubmitting || guard.settling}
              aria-describedby={`${fieldId}-summary`}
              className={cn(TOUCH, 'font-bold', SIDE_FILL[formAction])}
            >
              {isSubmitting ? 'Placing...' : `Place ${formAction} Order`}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
