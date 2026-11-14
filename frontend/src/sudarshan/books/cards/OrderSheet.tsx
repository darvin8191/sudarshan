import { useQuery } from '@tanstack/react-query'
import { Repeat } from 'lucide-react'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { cn } from '@/lib/utils'
import { type BookOrder, reasonOf } from '@/sudarshan/books/orderFold'
import { withoutCode } from '@/sudarshan/books/sendOutcome'
import { type Product, productsFor, ticketProduct } from '@/sudarshan/ticket/productPref'
import { contractOf } from '@/sudarshan/watchlist/api'
import { ActionWords, OUTLINED } from './parts'

/**
 * What an order's sheet gained in B2 (M6.1): the order type in words, the broker's reason for a
 * rejection without its error code, and Repeat, which opens the app's order ticket for the same
 * instrument, side and quantity, in the order's own product ("repeat" promises the same order;
 * v0.13.0 review M3). The price type is the ticket's own, and nothing is sent until the owner
 * presses the ticket's button. The sheet itself is OrderCards'.
 */

/** "Stop-loss limit (SL)": an order's price type in words. */
export function typeText(pricetype: unknown): string {
  const type = String(pricetype ?? '').toUpperCase()
  switch (type) {
    case 'MARKET':
      return 'Market'
    case 'LIMIT':
      return 'Limit'
    case 'SL':
      return 'Stop-loss limit (SL)'
    case 'SL-M':
      return 'Stop-loss market (SL-M)'
    default:
      return type || '-'
  }
}

/** The broker's reason for a rejection, in its own words without its error code, or ''. */
export function rejectionWords(reason: unknown): string {
  return withoutCode(reasonOf(reason))
}

/** The lot and tick size a ticket for this instrument needs, read once and shared. */
function useContract(apiKey: string | null, symbol: string, exchange: string) {
  return useQuery({
    queryKey: ['sudarshan', 'contract', exchange, symbol],
    queryFn: () => contractOf(apiKey ?? '', symbol, exchange),
    enabled: apiKey !== null,
    staleTime: 6 * 60 * 60_000,
    retry: false,
  })
}

/**
 * Repeat, on the sheet: offered once the lot size is known, since a derivative ticket counts in
 * lots and a guessed lot of one would misstate the quantity. Until then it says why it waits.
 */
export function RepeatButton({
  order,
  apiKey,
  sandbox,
  onRepeat,
}: {
  order: BookOrder
  apiKey: string | null
  /** Analyze mode: the ticket's order goes to the sandbox, and the button says so. */
  sandbox: boolean
  onRepeat: () => void
}) {
  const contract = useContract(apiKey, order.symbol, order.exchange)
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        aria-haspopup="dialog"
        disabled={!contract.data}
        onClick={onRepeat}
        className={cn(OUTLINED, 'w-full border-primary text-primary')}
      >
        <Repeat aria-hidden="true" className="size-4" />
        <ActionWords sandbox={sandbox}>Repeat</ActionWords>
      </button>
      {contract.isPending && apiKey !== null && (
        <p className="text-xs font-medium text-muted-foreground">Reading the lot size.</p>
      )}
      {contract.isError && (
        <p className="flex flex-wrap items-center gap-x-2 text-xs font-medium text-[color:var(--warning-text)]">
          The lot size could not be read, so the order ticket stays shut.
          <button
            type="button"
            onClick={() => void contract.refetch()}
            className="min-h-12 font-semibold text-[color:var(--brand)] underline underline-offset-2"
          >
            Try again
          </button>
        </p>
      )}
    </div>
  )
}

/**
 * The product Repeat opens the ticket with: the repeated order's own, the rule Add and Exit follow
 * on a position. An NRML order repeated on a phone whose pick is MIS would otherwise open as an
 * intraday order and be squared off the same day. One the segment does not take (or a book row
 * with none) falls back to the product every ticket opens with.
 */
export function repeatProduct(order: BookOrder): Product | undefined {
  const product = String(order.product ?? '').toUpperCase() as Product
  return productsFor(order.exchange).includes(product) ? product : ticketProduct(order.exchange)
}

/**
 * Repeat's ticket: the app's order ticket on the order's instrument, side and quantity, in the
 * order's own product. It opens once the lot size is known (RepeatButton waits for
 * the same read, so this one is already there).
 */
export function RepeatTicket({
  order,
  apiKey,
  onClose,
}: {
  order: BookOrder
  apiKey: string | null
  onClose: () => void
}) {
  const contract = useContract(apiKey, order.symbol, order.exchange)
  if (!contract.data) return null
  return (
    <PlaceOrderDialog
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      symbol={order.symbol}
      exchange={order.exchange}
      action={String(order.action).toUpperCase() === 'SELL' ? 'SELL' : 'BUY'}
      quantity={Number(order.quantity) || undefined}
      lotSize={contract.data.lotSize}
      tickSize={contract.data.tickSize}
      product={repeatProduct(order)}
      strategy="Orders"
    />
  )
}
