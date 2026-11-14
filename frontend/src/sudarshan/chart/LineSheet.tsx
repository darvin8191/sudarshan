/**
 * A chart line's sheet (M4.7, C6): what a tap on a line's label opens.
 *
 * - a position: **Exit** and **Add**;
 * - a working order: **Modify** and **Cancel**;
 * - a price alert: **Edit** and **Delete**.
 *
 * It is an entry point, not an order path: nothing here is a new way to send an
 * order. Exit is the Positions page's own close, asked first and held by
 * `usePositionExits` and `books/sent.ts` exactly as on that page; Cancel is the
 * Orders page's cancel behind the same confirmation and the same hold; Modify
 * sends the Orders page's modify (`books/orderSends.ts`); Add opens the app's
 * order ticket on the position's side and product. In Analyze mode they go
 * where the pages' go, to the sandbox, and say so. The sheet, each confirmation
 * and the form take no tap for their first half second (`openGuard.ts`).
 *
 * Lines too close on the chart to have a touch target each open here as a list
 * to pick from first.
 */
import { useQueryClient } from '@tanstack/react-query'
import { Bell, Briefcase, LogOut, Pencil, Plus, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { sendChange } from '@/sudarshan/alerts/actions'
import { units } from '@/sudarshan/books/cards/format'
import {
  cancelHeldWhy,
  cancelWarning,
  cancelWords,
  priceText,
  remainingText,
} from '@/sudarshan/books/cards/OrderCards'
import { exitWords, positionId, usePositionExits } from '@/sudarshan/books/cards/PositionCards'
import {
  ACTION,
  ActionWords,
  BUY_FILL,
  Confirm,
  Money,
  OUTLINED,
  SANDBOX_SENTENCE,
  SELL_FILL,
  useSandbox,
  WarningLine,
} from '@/sudarshan/books/cards/parts'
import type { BookOrder } from '@/sudarshan/books/orderFold'
import { cancelOrderSend, closePositionSend, modifyOrderSend } from '@/sudarshan/books/orderSends'
import {
  cancelMark,
  exitMark,
  isHeld,
  noteCancelBook,
  sendHeld,
  sentMarksNow,
  useSentMarks,
} from '@/sudarshan/books/sent'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import type { Order, Position } from '@/types/trading'
import { showToast } from '@/utils/toast'
import { positionPnl } from './LineLabels'
import type { ChartLine } from './overlays'
import type { PriceStore } from './prices'
import type { ChartBooks } from './useChartLines'

const FIELD = 'flex flex-col gap-1.5'
const LEGEND = 'text-xs font-semibold text-[color:var(--text-secondary)]'

/** Whether a number typed into the modify form can be sent. */
const positive = (text: string): number | null => {
  const value = Number(text)
  return text.trim() !== '' && Number.isFinite(value) && value > 0 ? value : null
}

export interface LineSheetProps {
  /** The lines the tapped label stood for; null while no label has been tapped. */
  ids: readonly string[] | null
  lines: readonly ChartLine[]
  books: ChartBooks
  apiKey: string | null
  /** The instrument's tick, for the modify form's steps; null until its details are read. */
  tick: number | null
  /** The order ticket can open: the instrument's lot size is known. */
  canAdd: boolean
  prices: PriceStore
  quiet: boolean
  onClose: () => void
  /** Open the app's order ticket to add to a position, on its side and product. */
  onAdd: (position: Position) => void
  /** Open the alert's own form. */
  onEditAlert: (id: number) => void
}

export function LineSheet({
  ids,
  lines,
  books,
  apiKey,
  tick,
  canAdd,
  prices,
  quiet,
  onClose,
  onAdd,
  onEditAlert,
}: LineSheetProps) {
  const sandbox = useSandbox()
  const client = useQueryClient()
  const marks = useSentMarks()
  // The exit hold's side of this screen, as on the Positions page: each book read is told
  // to the hold, an exit waiting on its own order has the order book read for it, and Exit
  // asks first. The page's own call is what it sends.
  const exits = usePositionExits({
    onClose: (position) => closePositionSend(position, () => void books.refresh()),
    onCloseAll: () => 'refused',
    positions: books.positions,
    bookAt: books.bookAt,
    loading: books.loading,
    apiKey,
  })
  // Each order book read tells the cancels sent in it, as the Orders page's cards do.
  useEffect(() => {
    if (books.bookAt !== null) noteCancelBook(books.sandbox, books.orders)
  }, [books.orders, books.bookAt, books.sandbox])

  const open = ids !== null
  const here = open ? lines.filter((line) => ids.includes(line.id)) : []
  const [picked, setPicked] = useState<string | null>(null)
  const [step, setStep] = useState<'actions' | 'modify'>('actions')
  const [asking, setAsking] = useState<'cancel' | 'delete' | null>(null)
  // A sheet opened again starts where a sheet starts.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the tap is the trigger
  useEffect(() => {
    setPicked(null)
    setStep('actions')
    setAsking(null)
  }, [ids?.join('|')])
  // Every line it stood for has gone (filled, cancelled, closed): nothing to act on.
  const gone = open && !books.loading && here.length === 0
  useEffect(() => {
    if (gone) onClose()
  }, [gone, onClose])

  const line = here.length === 1 ? here[0] : (here.find((one) => one.id === picked) ?? null)
  const ref = line?.ref ?? null
  const position = ref?.kind === 'position' ? ref.position : null
  const order = ref?.kind === 'order' ? ref.order : null
  const alert = ref?.kind === 'alert' ? ref.alert : null
  const cancelOf = order ? cancelMark(marks, sandbox, order.orderid) : undefined
  const cancelHeld = cancelWords(cancelOf)?.button ?? null
  const exitHeld = position
    ? (exitWords(exitMark(marks, sandbox, positionId(position)))?.button ?? null)
    : null
  const first = here[0]?.ref
  const named = first
    ? describe(
        first.kind === 'position'
          ? first.position.symbol
          : first.kind === 'order'
            ? first.order.symbol
            : first.alert.symbol,
        first.kind === 'position'
          ? first.position.exchange
          : first.kind === 'order'
            ? first.order.exchange
            : first.alert.exchange
      )
    : null

  return (
    <>
      <BottomSheet
        open={open && here.length > 0}
        onOpenChange={(next) => {
          if (!next) onClose()
        }}
        title={named?.title ?? ''}
        description={line ? line.label : `${here.length} lines here`}
        step={`${line?.id ?? 'pick'}:${step}`}
        aside={
          position && (
            <div className="pt-3">
              <LivePnl position={position} bookAt={books.bookAt} prices={prices} quiet={quiet} />
            </div>
          )
        }
      >
        {!line && (
          <ul className="flex flex-col gap-2" aria-label="Lines near this price">
            {here.map((one) => (
              <li key={one.id}>
                <button
                  type="button"
                  onClick={() => setPicked(one.id)}
                  className={cn(OUTLINED, 'w-full justify-start')}
                >
                  {one.kind === 'position' ? (
                    <Briefcase aria-hidden="true" className="size-4" />
                  ) : one.kind === 'alert' ? (
                    <Bell aria-hidden="true" className="size-4" />
                  ) : (
                    <Pencil aria-hidden="true" className="size-4" />
                  )}
                  {one.label}
                </button>
              </li>
            ))}
          </ul>
        )}

        {line?.stale && (
          <div className="pb-2">
            <WarningLine
              text={`The books could not be read just now: this line is ${line.note ?? 'not current'}. Check Positions or Orders before acting on it.`}
            />
          </div>
        )}

        {position && (
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              aria-haspopup="dialog"
              disabled={exitHeld !== null}
              onClick={() => exits.close(position)}
              className={cn(
                ACTION,
                'flex-col gap-0 py-1.5',
                Number(position.quantity) > 0 ? SELL_FILL : BUY_FILL
              )}
            >
              <span className="flex items-center gap-1.5 font-bold">
                <LogOut aria-hidden="true" className="size-4" />
                Exit
              </span>
              <span className="text-[0.71875rem] font-semibold">
                {exitHeld ??
                  `${Number(position.quantity) > 0 ? 'Sell' : 'Buy'} ${units(Math.abs(Number(position.quantity)))} ${sandbox ? 'in the sandbox' : 'at market'}`}
              </span>
            </button>
            <button
              type="button"
              aria-haspopup="dialog"
              disabled={!canAdd}
              onClick={() => onAdd(position)}
              className={cn(
                ACTION,
                'flex-col gap-0 py-1.5',
                Number(position.quantity) > 0 ? BUY_FILL : SELL_FILL
              )}
            >
              <span className="flex items-center gap-1.5 font-bold">
                <Plus aria-hidden="true" className="size-4" />
                Add
              </span>
              <span className="text-[0.71875rem] font-semibold">
                {Number(position.quantity) > 0 ? 'Buy more' : 'Sell more'}
                {sandbox ? ' in the sandbox' : ''}
              </span>
            </button>
          </div>
        )}

        {order && step === 'actions' && (
          <div className="flex flex-col gap-2">
            <p className="text-sm font-medium text-[color:var(--text-secondary)]">
              {order.action} {remainingText(order as BookOrder)} · {priceText(order as BookOrder)} ·{' '}
              {order.product}
            </p>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                disabled={cancelHeld !== null}
                onClick={() => setStep('modify')}
                className={cn(OUTLINED, 'border-primary text-primary')}
              >
                <Pencil aria-hidden="true" className="size-4" />
                <ActionWords sandbox={sandbox && cancelHeld === null}>Modify</ActionWords>
              </button>
              <button
                type="button"
                aria-haspopup="dialog"
                disabled={cancelHeld !== null}
                onClick={() => setAsking('cancel')}
                className={cn(OUTLINED, 'border-destructive text-destructive')}
              >
                <X aria-hidden="true" className="size-4" />
                {cancelHeld ?? (
                  <ActionWords sandbox={sandbox}>
                    {cancelOf?.expired ? 'Cancel again' : 'Cancel order'}
                  </ActionWords>
                )}
              </button>
            </div>
          </div>
        )}

        {order && step === 'modify' && (
          <ModifyForm
            key={order.orderid}
            order={order}
            tick={tick}
            sandbox={sandbox}
            held={cancelHeld !== null}
            onBack={() => setStep('actions')}
            onSend={async (form) => {
              const accepted = await modifyOrderSend(order, form, () => void books.refresh())
              if (accepted) onClose()
            }}
          />
        )}

        {alert && (
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              aria-haspopup="dialog"
              onClick={() => onEditAlert(alert.id)}
              className={cn(OUTLINED, 'border-primary text-primary')}
            >
              <Pencil aria-hidden="true" className="size-4" />
              Edit
            </button>
            <button
              type="button"
              aria-haspopup="dialog"
              onClick={() => setAsking('delete')}
              className={cn(OUTLINED, 'border-destructive text-destructive')}
            >
              <Trash2 aria-hidden="true" className="size-4" />
              Delete
            </button>
          </div>
        )}
      </BottomSheet>

      {exits.dialog}

      <Confirm
        open={asking === 'cancel' && order !== null}
        title="Cancel this order?"
        description={
          order ? (
            <>
              {cancelWarning(cancelOf) && <WarningLine text={cancelWarning(cancelOf) ?? ''} />}
              {`${order.action} ${remainingText(order as BookOrder)} ${named?.title ?? ''} · ${priceText(order as BookOrder)}. ${sandbox ? `The order stops working in the sandbox. ${SANDBOX_SENTENCE}` : 'The order stops working at the exchange.'}`}
            </>
          ) : (
            ''
          )
        }
        cancelLabel="Keep order"
        confirmLabel="Cancel order"
        onConfirm={() => {
          if (!order) return
          const { orderid } = order
          const mark = cancelMark(sentMarksNow(), sandbox, orderid)
          // Held since this opened: nothing is sent, and the toast says why.
          if (isHeld(mark)) return void showToast.warning(cancelHeldWhy(mark), 'orders')
          void sendHeld('cancel', sandbox, [{ id: orderid }], () =>
            cancelOrderSend(orderid, () => void books.refresh())
          )
        }}
        onClose={() => setAsking(null)}
      />

      <Confirm
        open={asking === 'delete' && alert !== null}
        title="Delete this alert?"
        description={alert ? `${line?.label ?? ''}. It stops watching; its history stays.` : ''}
        cancelLabel="Keep alert"
        confirmLabel="Delete"
        onConfirm={() => {
          if (alert) void sendChange(client, alert, 'delete')
        }}
        onClose={() => setAsking(null)}
      />
    </>
  )
}

function LivePnl({
  position,
  bookAt,
  prices,
  quiet,
}: {
  position: Position
  bookAt: number | null
  prices: PriceStore
  quiet: boolean
}) {
  const { tick } = useSyncExternalStore(prices.subscribe, prices.get)
  return (
    <Money
      value={positionPnl(position, bookAt, tick)}
      stale={quiet}
      className="sd-display text-[1.375rem] leading-none font-bold"
      arrowClassName="size-3"
    />
  )
}

/**
 * Quantity, and whichever of price and trigger the order's type carries, as the
 * Orders page's own dialog offers them; the rest of the order goes back as it
 * is. Modify sends once however it is tapped, and stays put if it is refused,
 * with the refusal in a toast, so the figures can be put right.
 */
function ModifyForm({
  order,
  tick,
  sandbox,
  held,
  onBack,
  onSend,
}: {
  order: Order
  tick: number | null
  sandbox: boolean
  /** A cancel is on its way for the order: nothing more is sent for it. */
  held: boolean
  onBack: () => void
  onSend: (form: {
    pricetype: string
    quantity: number
    price: number
    trigger_price: number
  }) => Promise<void>
}) {
  const withPrice = order.pricetype === 'LIMIT' || order.pricetype === 'SL'
  const withTrigger = order.pricetype === 'SL' || order.pricetype === 'SL-M'
  const [quantity, setQuantity] = useState(String(order.quantity))
  const [price, setPrice] = useState(String(order.price))
  const [trigger, setTrigger] = useState(String(order.trigger_price))
  const [busy, setBusy] = useState(false)
  const once = useRef(false)
  const q = positive(quantity)
  const wholeUnits = q !== null && Number.isInteger(q)
  const p = withPrice ? positive(price) : Number(order.price) || 0
  const t = withTrigger ? positive(trigger) : Number(order.trigger_price) || 0
  const valid = wholeUnits && p !== null && t !== null
  const step = tick && tick > 0 ? tick : 0.05
  const field = (
    id: string,
    label: string,
    value: string,
    set: (v: string) => void,
    bad: string | null,
    decimal: boolean
  ) => (
    <div className={FIELD}>
      <label htmlFor={id} className={LEGEND}>
        {label}
      </label>
      <Input
        id={id}
        type="number"
        inputMode={decimal ? 'decimal' : 'numeric'}
        min={0}
        step={decimal ? step : 1}
        value={value}
        onChange={(event) => set(event.target.value)}
        aria-invalid={bad !== null || undefined}
        aria-describedby={bad ? `${id}-problem` : undefined}
        className="h-12 text-base tabular-nums"
      />
      {bad && (
        <p id={`${id}-problem`} className="text-sm font-semibold text-[color:var(--down)]">
          {bad}
        </p>
      )}
    </div>
  )
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (!valid || held || once.current) return
        once.current = true
        setBusy(true)
        void onSend({
          pricetype: order.pricetype,
          quantity: q ?? 0,
          price: p ?? 0,
          trigger_price: t ?? 0,
        }).finally(() => {
          once.current = false
          setBusy(false)
        })
      }}
    >
      <p className="text-sm font-medium text-[color:var(--text-secondary)]">
        {order.action} · {order.pricetype} · {order.product}. Side, type and product stay as they
        are.
      </p>
      {field(
        'line-modify-quantity',
        'Quantity',
        quantity,
        setQuantity,
        wholeUnits ? null : 'Enter a whole quantity above 0.',
        false
      )}
      {withTrigger &&
        field(
          'line-modify-trigger',
          'Trigger price',
          trigger,
          setTrigger,
          t === null ? 'Enter a trigger price above 0.' : null,
          true
        )}
      {withPrice &&
        field(
          'line-modify-price',
          'Price',
          price,
          setPrice,
          p === null ? 'Enter a price above 0.' : null,
          true
        )}
      <div className="grid grid-cols-2 gap-2">
        <button type="button" onClick={onBack} className={OUTLINED}>
          Keep as is
        </button>
        <button
          type="submit"
          disabled={!valid || busy || held}
          className={cn(ACTION, 'bg-primary text-primary-foreground hover:bg-primary/90')}
        >
          {busy ? 'Modifying' : <ActionWords sandbox={sandbox}>Modify order</ActionWords>}
        </button>
      </div>
    </form>
  )
}
