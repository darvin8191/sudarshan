import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock,
  Hourglass,
  Info,
  RefreshCw,
  XCircle,
} from 'lucide-react'
import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'
import { TickPrice } from '@/sudarshan/books/LiveFigures'
import type { TickDirection } from '@/sudarshan/books/prices'
import { directionOf, formatPercent, formatPrice, formatRupees } from '@/sudarshan/dashboard/format'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { PRODUCT_MEANING, type Product } from '@/sudarshan/ticket/productPref'
import { useOpenGuard } from '@/sudarshan/watchlist/openGuard'
import { priceDecimals } from '@/sudarshan/watchlist/rows'
import { type OrderState, sideOf, signedUnits } from './format'

/**
 * The pieces every phone book is drawn from (M6.9), in the D look: white cards
 * on the grey page, 48dp targets, Barlow with tabular figures, Khand only for
 * the one big figure. Nothing is told by colour alone: a figure carries its
 * sign and arrow, a side its word, a status its word and icon, a stale price a
 * clock.
 */

/** Grey for a figure whose ticks have stopped; its sign and arrow stay. */
export const STALE = 'text-[color:var(--stale)]'

/** A 48dp action, as the watchlist's sheet draws them. */
export const ACTION =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'
export const OUTLINED = cn(ACTION, 'border border-input bg-card text-foreground hover:bg-accent')
export const BUY_FILL = 'bg-[color:var(--buy-solid)] text-white hover:opacity-90'
export const SELL_FILL = 'bg-[color:var(--sell-solid)] text-white hover:opacity-90'
export const DANGER_FILL = 'bg-destructive text-white hover:bg-destructive/90'

/**
 * An action that cannot be taken just now: grey, with a dashed edge and none
 * of its own colour, so it does not look like one that can (a pale red Exit
 * all beside "No open positions" did).
 */
export const UNAVAILABLE = cn(
  ACTION,
  'cursor-not-allowed border border-dashed border-[color:var(--border-strong)] bg-card text-[color:var(--text-tertiary)]'
)

/**
 * An action that cannot be taken just now, and why (`why`), or null when it
 * can. Its button stays reachable and reads as unavailable (aria-disabled,
 * described by the reason), and a tap or focus shows the reason beside it
 * (`WhyNot`) instead of doing nothing at all.
 */
export function useWhyNot(why: string | null) {
  const id = useId()
  // The reason shown, until focus leaves; a new or no reason hides it again.
  const [told, setTold] = useState<string | null>(null)
  const tell = () => setTold(why)
  return {
    why,
    id,
    told: why !== null && told === why,
    /** What the button takes: `act` when the action can be taken, else the reason. */
    button: (act: () => void) =>
      why === null
        ? { onClick: act }
        : {
            onClick: tell,
            onFocus: tell,
            onBlur: () => setTold(null),
            'aria-disabled': true as const,
            'aria-describedby': id,
          },
  }
}

/** Why an action cannot be taken: read with its button, and shown once it is tapped or focused. */
export function WhyNot({ why, id, told }: { why: string | null; id: string; told: boolean }) {
  if (why === null) return null
  return (
    <p
      id={id}
      data-testid="why-not"
      className={
        told
          ? 'flex items-start gap-1.5 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]'
          : 'sr-only'
      }
    >
      {told && <Info aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />}
      {why}
    </p>
  )
}

/** The white card one row of a book sits on. */
export const CARD =
  'flex min-h-14 w-full flex-col gap-1 rounded-[1rem] border bg-card px-4 py-3 text-left shadow-[var(--shadow-card)] outline-none active:bg-accent focus-visible:ring-2 focus-visible:ring-ring'

/** Money that moves with the market: its sign, its arrow, up or down colour; grey when stale. */
export function Money({
  value,
  stale = false,
  className,
  arrowClassName = 'size-2.5',
}: {
  value: number | null
  stale?: boolean
  className?: string
  arrowClassName?: string
}) {
  if (value === null) {
    return (
      <span className={cn('text-muted-foreground', className)}>
        <span aria-hidden="true">&mdash;</span>
        <span className="sr-only">Not known yet</span>
      </span>
    )
  }
  const direction = directionOf(value)
  return (
    <span
      data-direction={direction}
      data-stale={stale || undefined}
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap',
        stale ? STALE : TREND_TEXT[direction],
        className
      )}
    >
      <TrendArrow direction={direction} className={arrowClassName} />
      {formatRupees(value, { signed: true })}
    </span>
  )
}

/** A percentage with its sign, in the up or down colour; grey when stale. */
export function SignedPercent({
  value,
  stale = false,
  className,
}: {
  value: number | null
  stale?: boolean
  className?: string
}) {
  if (value === null) return null
  return (
    <span
      data-stale={stale || undefined}
      className={cn('whitespace-nowrap', stale ? STALE : TREND_TEXT[directionOf(value)], className)}
    >
      {formatPercent(value)}
    </span>
  )
}

/**
 * The last traded price with its last tick's arrow (`TickPrice`, as the wide
 * tables draw it); grey with a clock once its ticks have stopped.
 */
export function Last({
  value,
  tick,
  stale,
  exchange,
  label = 'LTP',
  className,
}: {
  value: number | null
  tick: TickDirection | null
  stale: boolean
  exchange: string
  label?: string | null
  className?: string
}) {
  const decimals = priceDecimals(exchange)
  return (
    <span className={cn('inline-flex items-center gap-1 whitespace-nowrap', className)}>
      {label && <span className="font-medium text-[color:var(--text-tertiary)]">{label}</span>}
      <TickPrice
        value={value ?? undefined}
        direction={tick}
        stale={stale}
        format={(price) => formatPrice(price, decimals)}
        className="font-semibold"
      />
      {stale && <Clock aria-hidden="true" className={cn('size-3 shrink-0', STALE)} />}
    </span>
  )
}

const CHIP =
  'inline-flex h-6 shrink-0 items-center gap-1 rounded-md px-1.5 text-[0.71875rem] leading-none font-bold tracking-wide'

/** MIS, NRML or CNC; what it means is in the sheet. */
export function ProductChip({ product }: { product: string }) {
  const meaning = PRODUCT_MEANING[product as Product]
  return (
    <span
      title={meaning}
      className={cn(CHIP, 'bg-[color:var(--surface-2)] text-[color:var(--text-secondary)]')}
    >
      {product || '-'}
    </span>
  )
}

/** BUY or SELL, white on its solid colour. */
export function SideChip({ side }: { side: string }) {
  const word = String(side).toUpperCase()
  return (
    <span
      className={cn(
        CHIP,
        'px-2 text-white',
        word === 'SELL' ? 'bg-[color:var(--sell-solid)]' : 'bg-[color:var(--buy-solid)]'
      )}
    >
      {word}
    </span>
  )
}

/** +65 LONG or −30 SHORT: a position's quantity with its sign and its side in words. */
export function PositionSide({ quantity }: { quantity: number }) {
  const side = sideOf(quantity)
  if (side === null) {
    return <span className="text-[0.8125rem] font-semibold text-muted-foreground">Closed</span>
  }
  return (
    <span
      data-side={side}
      className={cn(
        'inline-flex items-center gap-1 text-[0.8125rem] font-bold whitespace-nowrap',
        side === 'LONG' ? 'text-[color:var(--buy-solid)]' : 'text-[color:var(--sell-solid)]'
      )}
    >
      {signedUnits(quantity)}
      <span className="text-[0.71875rem] tracking-wide">{side}</span>
    </span>
  )
}

const STATUS_LOOK: Record<OrderState['tone'], { className: string; icon: typeof Clock }> = {
  up: { className: 'bg-[color:var(--up-tint)] text-[color:var(--up)]', icon: CheckCircle2 },
  down: { className: 'bg-[color:var(--down-tint)] text-[color:var(--down)]', icon: XCircle },
  working: {
    className: 'bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]',
    icon: Clock,
  },
  muted: { className: 'bg-[color:var(--surface-2)] text-[color:var(--neutral)]', icon: Ban },
}

/** An order's status: its word and an icon, on a tint of its colour. */
export function StatusChip({ state }: { state: OrderState }) {
  const look = STATUS_LOOK[state.tone]
  const Icon = look.icon
  return (
    <span data-status={state.label} className={cn(CHIP, 'px-2', look.className)}>
      <Icon aria-hidden="true" className="size-3.5" />
      {state.label}
    </span>
  )
}

/** Whether an order from here goes to the sandbox: Analyze mode is on. */
export function useSandbox(): boolean {
  return useThemeStore((state) => state.appMode) === 'analyzer'
}

/** What a confirmation says in Analyze mode, after where the order goes. */
export const SANDBOX_SENTENCE = 'Analyze mode is on, so nothing goes to Upstox.'

/** An action's words, and in Analyze mode a second line: its order goes to the sandbox. */
export function ActionWords({ children, sandbox }: { children: ReactNode; sandbox: boolean }) {
  if (!sandbox) return <>{children}</>
  return (
    <span className="flex flex-col items-center leading-tight">
      <span>{children}</span>{' '}
      <span className="text-[0.71875rem] font-semibold">in the sandbox</span>
    </span>
  )
}

/** "Exit sent", "Cancel sent": a row whose order went and whose book has not caught up yet. */
export function SentChip({ label }: { label: string }) {
  return (
    <span
      data-sent="true"
      className={cn(CHIP, 'px-2 bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]')}
    >
      <Hourglass aria-hidden="true" className="size-3.5" />
      {label}
    </span>
  )
}

/** A card's line when no answer came back for its exit or cancel: what to do first. */
export function SentNote({ text }: { text: string }) {
  return (
    <span
      data-sent="unconfirmed"
      className="flex items-start gap-1.5 rounded-[0.625rem] bg-[color:var(--gold-tint)] px-2.5 py-1.5 text-xs leading-snug font-semibold text-[color:var(--warning-text)]"
    >
      <AlertTriangle aria-hidden="true" className="mt-px size-3.5 shrink-0" />
      {text}
    </span>
  )
}

/** A confirmation's first line when an earlier order may still be working. */
export function WarningLine({ text }: { text: string }) {
  return (
    <span
      data-warning="true"
      className="mb-2 flex items-start gap-1.5 font-semibold text-[color:var(--warning-text)]"
    >
      <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      {text}
    </span>
  )
}

/** Refresh as a 48dp icon, for a row with no room for its word. */
export function RefreshIcon({
  refreshing,
  onRefresh,
}: {
  refreshing: boolean
  onRefresh: () => void
}) {
  return (
    <button
      type="button"
      onClick={onRefresh}
      disabled={refreshing}
      aria-label={refreshing ? 'Refreshing' : 'Refresh'}
      className={cn(OUTLINED, 'w-12 shrink-0 px-0')}
    >
      <RefreshCw aria-hidden="true" className={cn('size-4', refreshing && 'animate-spin')} />
    </button>
  )
}

/** The page's title, as the watchlist sets it, with Refresh beside it. */
export function BookHeading({
  title,
  refreshing,
  onRefresh,
}: {
  title: string
  refreshing: boolean
  onRefresh: () => void
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h1 className="sd-display text-[1.75rem] leading-none font-bold">{title}</h1>
      <button type="button" onClick={onRefresh} disabled={refreshing} className={OUTLINED}>
        <RefreshCw aria-hidden="true" className={cn('size-4', refreshing && 'animate-spin')} />
        {refreshing ? 'Refreshing' : 'Refresh'}
      </button>
    </div>
  )
}

/** A label and its value, one per line, in a sheet. Rows whose value is not known are left out. */
export function Details({
  rows,
  label = 'Details',
}: {
  rows: Array<{ label: string; value: ReactNode } | null | false>
  label?: string
}) {
  const shown = rows.filter((row): row is { label: string; value: ReactNode } => Boolean(row))
  if (shown.length === 0) return null
  return (
    <section aria-label={label} className="flex flex-col gap-1">
      <h3 className="text-sm font-bold">{label}</h3>
      <dl className="divide-y divide-[color:var(--divider)] rounded-[0.875rem] border px-3">
        {shown.map((row) => (
          <div key={row.label} className="flex min-h-11 items-center justify-between gap-3 py-2">
            <dt className="text-sm font-medium text-[color:var(--text-secondary)]">{row.label}</dt>
            <dd className="text-right text-sm font-semibold">{row.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

/** A plain price, in the exchange's decimals. */
export function price(value: number | null, exchange: string): string {
  return value === null ? '-' : formatPrice(value, priceDecimals(exchange))
}

/**
 * A yes-or-no step before an order goes out, the watchlist's dialog with 48dp
 * buttons. Its action runs once however fast it is tapped: the dialog stays on
 * screen until Back has taken its entry away, and a second tap in that moment
 * must not send a second order. For half a second after it opens it takes no
 * tap at all (`useOpenGuard`), and its action shows as disabled: on a tall
 * phone the sheet's Cancel and this dialog's "Cancel order" sit on the same
 * spot, so the second tap of a double tap on the one lands on the other.
 */
export function Confirm({
  open,
  title,
  description,
  cancelLabel = 'Cancel',
  confirmLabel,
  confirmClassName = DANGER_FILL,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  description: ReactNode
  cancelLabel?: string
  confirmLabel: string
  confirmClassName?: string
  onConfirm: () => void
  onClose: () => void
}) {
  const [sent, setSent] = useState(false)
  const once = useRef(false)
  const guard = useOpenGuard(open)
  useEffect(() => {
    if (open) {
      once.current = false
      setSent(false)
    }
  }, [open])

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        {...guard.handlers}
        data-settling={guard.settling || undefined}
        showCloseButton={false}
        className="rounded-[1.125rem] sm:max-w-sm"
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter className="flex-row gap-2">
          <button type="button" onClick={onClose} className={cn(OUTLINED, 'flex-1 sm:flex-none')}>
            {cancelLabel}
          </button>
          <button
            type="button"
            disabled={sent || guard.settling}
            onClick={() => {
              if (once.current) return
              once.current = true
              setSent(true)
              onConfirm()
              onClose()
            }}
            className={cn(ACTION, 'flex-1 sm:flex-none', confirmClassName)}
          >
            {confirmLabel}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Nothing to show, said plainly. */
export function EmptyCard({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-[1.125rem] border bg-card px-6 py-8 text-center shadow-[var(--shadow-card)]">
      <p className="text-base font-bold">{title}</p>
      {hint && <p className="max-w-xs text-sm text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** A read that failed: what happened, and a way to try again. */
export function ProblemCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section
      role="alert"
      className="flex flex-col items-start gap-3 rounded-[1.125rem] border border-destructive bg-card px-4 py-3.5"
    >
      <p className="flex items-center gap-2 text-sm font-semibold">
        <AlertTriangle
          aria-hidden="true"
          className="size-4 shrink-0 text-[color:var(--warning-text)]"
        />
        {message}
      </p>
      <button type="button" onClick={onRetry} className={OUTLINED}>
        <RefreshCw aria-hidden="true" className="size-4" />
        Try again
      </button>
    </section>
  )
}

/** The last read failed while older figures are on screen: they stay, and say so. */
export function StaleBookNote({ text }: { text: string }) {
  return (
    <output className="flex items-start gap-1.5 px-1 text-[0.78125rem] font-medium">
      <AlertTriangle
        aria-hidden="true"
        className="mt-px size-3.5 shrink-0 text-[color:var(--warning-text)]"
      />
      {text}
    </output>
  )
}

/** Cards that are still being read. */
export function LoadingCards({ count = 3 }: { count?: number }) {
  return (
    <ul aria-label="Loading" aria-busy="true" className="flex flex-col gap-2">
      {Array.from({ length: count }, (_, index) => (
        <li
          key={index}
          className="flex min-h-[5.25rem] flex-col justify-center gap-2 rounded-[1rem] border bg-card px-4"
        >
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-3 w-28" />
        </li>
      ))}
    </ul>
  )
}
