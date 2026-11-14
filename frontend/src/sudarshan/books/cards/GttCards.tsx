import { CandlestickChart, Pencil, X } from 'lucide-react'
import { memo, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSocketContext } from '@/components/socket/SocketProvider'
import type { OrderEventType } from '@/hooks/useOrderEventRefresh'
import { cn } from '@/lib/utils'
import { AccessNotice } from '@/sudarshan/AccessNotice'
import { usePriceAccess, useReadWhenReady } from '@/sudarshan/access'
import { useMarketCalendar } from '@/sudarshan/books/market'
import {
  gttMark,
  isHeld,
  noteGttBook,
  type SentMark,
  sendHeld,
  sentMarksNow,
  useSentMarks,
} from '@/sudarshan/books/sent'
import { istTime } from '@/sudarshan/books/time'
import { useBookSync } from '@/sudarshan/books/useBookSync'
import { SymbolChartLink } from '@/sudarshan/chart/SymbolChartLink'
import { PRODUCT_MEANING, type Product } from '@/sudarshan/ticket/productPref'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import type { GttLeg, GttOrder } from '@/types/trading'
import { showToast } from '@/utils/toast'
import { figure, type OrderState, units } from './format'
import { cancelWords } from './OrderCards'
import {
  ActionWords,
  CARD,
  Confirm,
  Details,
  EmptyCard,
  LoadingCards,
  OUTLINED,
  ProblemCard,
  price,
  RefreshIcon,
  SANDBOX_SENTENCE,
  SentChip,
  SentNote,
  SideChip,
  StaleBookNote,
  StatusChip,
  useSandbox,
  WarningLine,
} from './parts'
import { useBookOverlay, usePageDialog } from './useBookOverlay'
import { useHeld, useLatest } from './useLatest'

/**
 * The GTT book on a phone (B1: M5.6, M6.1), in place of the GTT tab's wide table: a card per
 * GTT with its side, quantity, trigger, status in words and when it was set, and a sheet per
 * GTT. Modify opens the tab's own modify dialog and Cancel makes the tab's own cancel call
 * after a confirmation; a cancel sent is held app-wide (books/sent.ts) until the GTT book
 * shows the GTT gone or ended.
 *
 * Upstox pushes nothing for its GTT book, so it is read again when the tab opens, after the
 * tab's own Modify or Cancel, when the app says a GTT changed, when an order arrives for an
 * instrument that has a GTT (a GTT that fires becomes an order), on coming back to the page,
 * on a Socket.IO reconnect and every 45 s while a market is open (useBookSync). The list says
 * when it was read; a read that fails keeps the GTTs last read and says so, with their time.
 */

export interface GttCardsProps {
  gtts: GttOrder[]
  loading: boolean
  refreshing: boolean
  error: string | null
  onRefresh: (showRefresh?: boolean) => unknown
  /** Opens the tab's own modify dialog on this GTT. */
  onModify: (gtt: GttOrder) => unknown
  /** The tab's cancel call, answering what became of it. */
  onCancel: (triggerId: string) => unknown
  /** Whether the tab's modify dialog is open. */
  modifyOpen: boolean
  onModifyOpenChange: (open: boolean) => void
}

/** The step the tab's own modify dialog shows. */
const MODIFY_ACTS = ['modify'] as const

/** What shows when the GTT book has never been read on this screen. */
export const GTT_NOT_READ = 'The GTT book could not be read just now.'

/** A GTT's status as its chip reads; Modify and Cancel only where the tab offers them. */
const STATES: Record<string, OrderState> = {
  active: { label: 'ACTIVE', tone: 'working', filter: null, working: true },
  disabled: { label: 'DISABLED', tone: 'muted', filter: null, working: true },
  triggered: { label: 'TRIGGERED', tone: 'up', filter: null, working: false },
  rejected: { label: 'REJECTED', tone: 'down', filter: null, working: false },
  cancelled: { label: 'CANCELLED', tone: 'muted', filter: null, working: false },
  expired: { label: 'EXPIRED', tone: 'muted', filter: null, working: false },
  deleted: { label: 'DELETED', tone: 'muted', filter: null, working: false },
}

/** Statuses outside those words, as met, one object each so a card is not redrawn for a copy. */
const OTHER = new Map<string, OrderState>()

export function gttState(status: unknown): OrderState {
  const word = String(status ?? '')
    .trim()
    .toLowerCase()
  if (STATES[word]) return STATES[word]
  const label = word.toUpperCase() || 'UNKNOWN'
  let state = OTHER.get(label)
  if (!state) {
    state = { label, tone: 'muted', filter: null, working: false }
    if (OTHER.size < 50) OTHER.set(label, state)
  }
  return state
}

/** BUY or SELL: the side of its legs (an OCO pair's are one side), or null when it has none. */
function sideOf(gtt: GttOrder): string | null {
  return String(gtt.legs?.[0]?.action ?? '').toUpperCase() || null
}

function quantityOf(gtt: GttOrder): string {
  const quantity = figure(gtt.legs?.[0]?.quantity)
  return quantity === null ? '-' : units(quantity)
}

/** "Trigger 1,240.00", or an OCO pair's two: "Triggers 1,240.00 and 1,400.00". */
export function triggerText(gtt: GttOrder): string {
  const prices = (gtt.trigger_prices ?? [])
    .map((value) => figure(value))
    .filter((value): value is number => value !== null && value > 0)
    .map((value) => price(value, gtt.exchange))
  if (prices.length === 0) return 'Trigger -'
  return prices.length === 1 ? `Trigger ${prices[0]}` : `Triggers ${prices.join(' and ')}`
}

/** What one leg places when the GTT fires: "SELL 50 · Limit 1,240.00". */
function legText(leg: GttLeg, exchange: string): string {
  const quantity = figure(leg.quantity)
  const type = String(leg.pricetype ?? '').toUpperCase()
  const at = price(figure(leg.price), exchange)
  const how = type === 'LIMIT' ? `Limit ${at}` : type ? `${type} ${at}` : at
  return `${String(leg.action ?? '').toUpperCase()} ${quantity === null ? '-' : units(quantity)} · ${how}`
}

/**
 * A GTT book's time as a moment. Upstox's carry their zone (UTC); the sandbox's are India's
 * time written without one, and are read as such.
 */
export function gttMoment(text: unknown): number | null {
  if (typeof text !== 'string' || text.trim() === '') return null
  const value = text.trim()
  const at = Date.parse(/(z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}+05:30`)
  return Number.isNaN(at) ? null : at
}

const SET_AT = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const DAY = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

/** "27 Aug, 18:03", India's time; empty when the book gave none. */
export function setAtText(text: unknown): string {
  const at = gttMoment(text)
  return at === null ? '' : SET_AT.format(at)
}

function dayText(text: unknown): string {
  const at = gttMoment(text)
  return at === null ? '' : DAY.format(at)
}

/** The instruments that have a GTT: by exchange and symbol, and by symbol alone. */
interface Watched {
  keys: ReadonlySet<string>
  symbols: ReadonlySet<string>
}

function watchedOf(gtts: readonly GttOrder[]): Watched {
  return {
    keys: new Set(gtts.map((gtt) => `${String(gtt.exchange).toUpperCase()}:${gtt.symbol}`)),
    symbols: new Set(gtts.map((gtt) => gtt.symbol)),
  }
}

/**
 * Whether an order event may have changed the GTT book: any sandbox update but a read (a
 * sandbox GTT that fires, or one set or cancelled through the app; useBookSync drops the
 * reads), or an order pushed or placed for an instrument with a GTT (one that fired at
 * Upstox becomes an order there).
 */
export function gttBookMayChange(event: OrderEventType, frame: unknown, watched: Watched): boolean {
  if (event === 'analyzer_update') return true
  if (event !== 'order_update' && event !== 'order_event') return false
  const f = (frame ?? {}) as Record<string, unknown>
  const symbol = typeof f.symbol === 'string' ? f.symbol.trim() : ''
  if (!symbol) return false
  const exchange = typeof f.exchange === 'string' ? f.exchange.trim().toUpperCase() : ''
  return exchange ? watched.keys.has(`${exchange}:${symbol}`) : watched.symbols.has(symbol)
}

/** What a confirmation says first when an earlier cancel's time ran out with the GTT active. */
export function gttCancelWarning(mark: SentMark | undefined): string | null {
  if (!mark?.expired) return null
  return `A cancel was sent at ${istTime(mark.sentAt)} and the GTT still shows active.`
}

/** Why a tap on Cancel GTT sends nothing: its cancel is still held. */
export function gttCancelHeldWhy(mark: SentMark): string {
  if (mark.state === 'sending') {
    return 'A cancel for this GTT is still being sent. Wait for its answer before cancelling again.'
  }
  if (mark.noAnswer) {
    return `A cancel for this GTT was sent at ${istTime(mark.sentAt)} and no answer has come yet: it may still be going through. Wait for the GTT tab to show it gone before cancelling again.`
  }
  const where = mark.state === 'unconfirmed' ? 'is not confirmed' : 'has not shown in the book yet'
  return `A cancel for this GTT was sent at ${istTime(mark.sentAt)} and ${where}. Check the GTT tab before cancelling again.`
}

const NONE: GttOrder[] = []

export function GttCards(props: GttCardsProps) {
  const onRefresh = useLatest(props.onRefresh)
  const onModify = useLatest(props.onModify)
  const onCancel = useLatest(props.onCancel)
  const onModifyOpenChange = useLatest(props.onModifyOpenChange)
  return (
    <GttBoard
      {...props}
      onRefresh={onRefresh}
      onModify={onModify}
      onCancel={onCancel}
      onModifyOpenChange={onModifyOpenChange}
    />
  )
}

const GttBoard = memo(function GttBoard({
  gtts,
  loading,
  refreshing,
  error,
  onRefresh,
  onModify,
  onCancel,
  modifyOpen,
  onModifyOpenChange,
}: GttCardsProps) {
  // Analyze mode: the book is the sandbox's, and so is every modify and cancel from it.
  const sandbox = useSandbox()
  const refresh = useCallback(() => void onRefresh(true), [onRefresh])
  // No key or no Upstox login: the notice says which, never "No GTT orders".
  const access = usePriceAccess()
  useReadWhenReady(access.state, error !== null, refresh)

  // The last book read, the time it came and the book it is (a mode switch alone changes
  // nothing until the other book is here). Each one read lets go of the cancels it settles.
  const [read, setRead] = useState<{ gtts: GttOrder[]; at: number; sandbox: boolean } | null>(null)
  const landed = useRef<GttOrder[] | null>(null)
  useEffect(() => {
    if (loading || error !== null || landed.current === gtts) return
    landed.current = gtts
    setRead({ gtts, at: Date.now(), sandbox })
    noteGttBook(
      sandbox,
      gtts.map((gtt) => ({ id: String(gtt.trigger_id), working: gttState(gtt.status).working }))
    )
  }, [gtts, loading, error, sandbox])
  const shown = read !== null && read.sandbox === sandbox ? read : null
  const rows = shown?.gtts ?? NONE

  // Read again on order events that may have changed the book, on return, on a reconnect and
  // every 45 s while a market is open; and whenever the app says a GTT changed.
  const watched = useMemo(() => watchedOf(rows), [rows])
  const { anyOpen } = useMarketCalendar()
  const sync = useBookSync(onRefresh, {
    marketOpen: anyOpen,
    accept: (event, frame) => gttBookMayChange(event, frame, watched),
  })
  const { socket } = useSocketContext()
  useEffect(() => {
    if (!socket) return
    const onGtt = () => void sync()
    socket.on('gtt_event', onGtt)
    return () => {
      socket.off('gtt_event', onGtt)
    }
  }, [socket, sync])

  const { overlay, open, swap, close } = useBookOverlay({
    dialog: { open: modifyOpen, acts: MODIFY_ACTS },
  })
  const openSheet = useCallback((id: string) => open({ act: 'sheet', row: id }), [open])
  const target =
    overlay?.row != null
      ? (rows.find((gtt) => String(gtt.trigger_id) === overlay.row) ?? null)
      : null
  const targetState = target ? gttState(target.status) : null
  // Something open over a GTT the book no longer has, or a cancel for one that has ended, is
  // closed once the book is known. A modify stays: the tab's dialog says what the broker answers.
  const orphan =
    overlay !== null &&
    shown !== null &&
    overlay.act !== 'modify' &&
    (target === null || (overlay.act === 'cancel' && !targetState?.working))
  useEffect(() => {
    if (orphan) close()
  }, [orphan, close])
  const modifyOpening = usePageDialog({
    entered: overlay?.act === 'modify',
    open: modifyOpen,
    setOpen: onModifyOpenChange,
    close,
  })

  // A cancel sent holds the GTT's Modify and Cancel until the GTT book shows it gone.
  const marks = useSentMarks()
  const cancelOf = (id: string) => gttMark(marks, sandbox, id)
  const cancelling = overlay?.act === 'cancel' && targetState?.working ? target : null
  const cancelShown = useHeld(cancelling)
  const cancelEarlier = gttCancelWarning(
    cancelShown ? cancelOf(String(cancelShown.trigger_id)) : undefined
  )

  let content: ReactNode
  if (access.state !== 'ready') {
    content = <AccessNotice access={access} />
  } else if (shown === null && error !== null && !loading) {
    content = <ProblemCard message={GTT_NOT_READ} onRetry={refresh} />
  } else if (shown === null) {
    content = <LoadingCards />
  } else {
    content = (
      <>
        {error && (
          <StaleBookNote
            text={`Could not refresh just now. Showing the GTTs read at ${istTime(shown.at)}.`}
          />
        )}
        {rows.length === 0 ? (
          <EmptyCard
            title="No GTT orders"
            hint="A GTT you place shows here until it fires, expires or is cancelled."
          />
        ) : (
          <ul aria-label="GTT orders" className="flex flex-col gap-2">
            {rows.map((gtt) => {
              const id = String(gtt.trigger_id)
              const state = gttState(gtt.status)
              const words = state.working ? cancelWords(cancelOf(id)) : null
              return (
                <GttCard
                  key={id}
                  id={id}
                  symbol={gtt.symbol}
                  exchange={gtt.exchange}
                  side={sideOf(gtt)}
                  product={String(gtt.legs?.[0]?.product ?? '')}
                  kind={gtt.trigger_type === 'two-leg' ? 'OCO' : 'Single'}
                  quantity={quantityOf(gtt)}
                  triggers={triggerText(gtt)}
                  state={state}
                  setAt={setAtText(gtt.created_at)}
                  sentChip={words?.chip ?? null}
                  sentNote={words?.note ?? null}
                  onOpen={openSheet}
                />
              )
            })}
          </ul>
        )}
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2" data-testid="gtt-actions">
        <p className="min-w-0 px-1 text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
          {shown ? `Read at ${istTime(shown.at)}` : ''}
        </p>
        <RefreshIcon refreshing={refreshing} onRefresh={refresh} />
      </div>

      {content}

      {overlay?.act === 'sheet' && target && targetState && (
        <GttSheet
          gtt={target}
          state={targetState}
          sandbox={sandbox}
          held={cancelWords(cancelOf(String(target.trigger_id)))?.button ?? null}
          again={cancelOf(String(target.trigger_id))?.expired === true}
          onClose={close}
          onModify={() => {
            modifyOpening()
            void onModify(target)
            swap({ act: 'modify', row: String(target.trigger_id) })
          }}
          onCancel={() => swap({ act: 'cancel', row: String(target.trigger_id) })}
        />
      )}

      <Confirm
        open={cancelling !== null}
        title="Cancel this GTT?"
        description={
          cancelShown ? (
            <>
              {cancelEarlier && <WarningLine text={cancelEarlier} />}
              {`${[sideOf(cancelShown), quantityOf(cancelShown), describe(cancelShown.symbol, cancelShown.exchange).title].filter(Boolean).join(' ')} · ${triggerText(cancelShown)}. ${sandbox ? `It is removed from the sandbox. ${SANDBOX_SENTENCE}` : 'It is removed at Upstox and will not fire.'}`}
            </>
          ) : (
            ''
          )
        }
        cancelLabel="Keep GTT"
        confirmLabel="Cancel GTT"
        onConfirm={() => {
          if (!cancelling) return
          const id = String(cancelling.trigger_id)
          const mark = gttMark(sentMarksNow(), sandbox, id)
          // Held since this opened: nothing is sent, and the toast says why.
          if (isHeld(mark)) return void showToast.warning(gttCancelHeldWhy(mark), 'orders')
          void sendHeld('gtt', sandbox, [{ id }], () => onCancel(id))
        }}
        onClose={close}
      />
    </div>
  )
})

/** One GTT: redrawn only when its own figures change. */
const GttCard = memo(function GttCard({
  id,
  symbol,
  exchange,
  side,
  product,
  kind,
  quantity,
  triggers,
  state,
  setAt,
  sentChip,
  sentNote,
  onOpen,
}: {
  id: string
  symbol: string
  exchange: string
  side: string | null
  product: string
  kind: string
  quantity: string
  triggers: string
  state: OrderState
  setAt: string
  /** Its cancel is held: "Cancel sent". */
  sentChip: string | null
  /** Its cancel got no answer: what to do before cancelling again. */
  sentNote: string | null
  onOpen: (id: string) => void
}) {
  const named = describe(symbol, exchange)
  return (
    <li data-gtt={id} data-status={state.label}>
      <button type="button" aria-haspopup="dialog" onClick={() => onOpen(id)} className={CARD}>
        <span className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <span className="flex min-w-0 max-w-full items-center gap-2">
            {side && <SideChip side={side} />}
            <span
              className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold"
              title={symbol}
            >
              {named.title}
            </span>
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            {sentChip && <SentChip label={sentChip} />}
            <StatusChip state={state} />
          </span>
        </span>
        <span className="flex flex-wrap items-baseline justify-between gap-x-3 text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
          <span className="min-w-0 truncate">
            {named.detail} · {product || '-'} · {kind}
          </span>
          {setAt && <span className="shrink-0">Created {setAt}</span>}
        </span>
        <span className="text-[0.8125rem] leading-tight">
          <span className="font-bold">Qty {quantity}</span>
          <span className="text-[color:var(--text-secondary)]"> · {triggers}</span>
        </span>
        {sentNote && <SentNote text={sentNote} />}
      </button>
    </li>
  )
})

/** One GTT's sheet: Modify and Cancel while it can still fire, its chart, and its details. */
function GttSheet({
  gtt,
  state,
  sandbox,
  held,
  again,
  onClose,
  onModify,
  onCancel,
}: {
  gtt: GttOrder
  state: OrderState
  /** Analyze mode: Modify and Cancel go to the sandbox, and say so. */
  sandbox: boolean
  /** Its cancel is held: Modify and Cancel wait, and Cancel says why. */
  held: string | null
  /** An earlier cancel's time ran out with the GTT still active. */
  again: boolean
  onClose: () => void
  onModify: () => void
  onCancel: () => void
}) {
  const named = describe(gtt.symbol, gtt.exchange)
  const side = sideOf(gtt)
  const legs = gtt.legs ?? []
  const product = String(legs[0]?.product ?? '')
  const meaning = PRODUCT_MEANING[product as Product]
  const fired = legs.map((leg) => leg.triggered_order_id).filter(Boolean)
  return (
    <BottomSheet
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title={named.title}
      description={named.detail}
      aside={
        <div className="pt-2.5">
          <StatusChip state={state} />
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          {side && <SideChip side={side} />}
          <span className="font-bold">Qty {quantityOf(gtt)}</span>
          <span className="text-[color:var(--text-secondary)]">{triggerText(gtt)}</span>
        </p>

        {state.working && (
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              aria-haspopup="dialog"
              disabled={held !== null}
              onClick={onModify}
              className={cn(OUTLINED, 'border-primary text-primary')}
            >
              <Pencil aria-hidden="true" className="size-4" />
              <ActionWords sandbox={sandbox && held === null}>Modify</ActionWords>
            </button>
            <button
              type="button"
              aria-haspopup="dialog"
              disabled={held !== null}
              onClick={onCancel}
              className={cn(OUTLINED, 'border-destructive text-destructive')}
            >
              <X aria-hidden="true" className="size-4" />
              {held ?? (
                <ActionWords sandbox={sandbox}>{again ? 'Cancel again' : 'Cancel GTT'}</ActionWords>
              )}
            </button>
          </div>
        )}

        <SymbolChartLink
          symbol={gtt.symbol}
          exchange={gtt.exchange}
          className={cn(OUTLINED, 'w-full')}
        >
          <CandlestickChart aria-hidden="true" className="size-5" />
          Chart
        </SymbolChartLink>

        <Details
          rows={[
            {
              label: 'Type',
              value: gtt.trigger_type === 'two-leg' ? 'OCO, two triggers' : 'Single trigger',
            },
            ...legs.map((leg, index) => ({
              label: legs.length > 1 ? `Order ${index + 1} when it fires` : 'Order when it fires',
              value: legText(leg, gtt.exchange),
            })),
            { label: 'Product', value: meaning ? `${product} · ${meaning}` : product || '-' },
            setAtText(gtt.created_at) !== '' && {
              label: 'Created',
              value: setAtText(gtt.created_at),
            },
            dayText(gtt.expires_at) !== '' && { label: 'Expires', value: dayText(gtt.expires_at) },
            fired.length > 0 && { label: 'Order placed', value: fired.join(', ') },
            { label: 'GTT ID', value: String(gtt.trigger_id) },
          ]}
        />
      </div>
    </BottomSheet>
  )
}
