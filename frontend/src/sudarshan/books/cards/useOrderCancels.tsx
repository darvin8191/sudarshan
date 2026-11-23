import { type ReactNode, useEffect, useRef, useState } from 'react'
import type { BookOrder } from '@/sudarshan/books/orderFold'
import {
  cancelMark,
  isHeld,
  marksOf,
  noteCancelBook,
  sendHeld,
  sentMarksNow,
  useSentMarks,
} from '@/sudarshan/books/sent'
import type { Order } from '@/types/trading'
import { showToast } from '@/utils/toast'
import { orderState } from './format'
import {
  cancelAllSentence,
  cancelHeldWhy,
  cancelSentence,
  cancelWarning,
  cancelWords,
  EVERY_CANCEL_SENT,
  NOTHING_TO_CANCEL,
} from './OrderCards'
import { Confirm, SentChip, SentNote, useSandbox, WarningLine } from './parts'
import { useHeld } from './useLatest'

/**
 * The wide Order Book's Cancel and Cancel all (test day TD4, M6.9), as the phone's cards do them:
 * each asks first in the cards' words, sends once however it is tapped, and stays held
 * (books/sent.ts, app-wide) until the book shows the order final. Only the question and the hold
 * are added: the cancel and cancel-all calls are the page's own, unchanged.
 */
export function useOrderCancels({
  orders,
  loading,
  onCancel,
  onCancelAll,
}: {
  orders: Order[]
  loading: boolean
  /** The page's cancel call. */
  onCancel: (orderid: string) => unknown
  /** The page's cancel-all call. */
  onCancelAll: () => unknown
}): {
  ask: (order: Order) => void
  askAll: () => void
  dialog: ReactNode
  /** v0.17.0 review L4: its cancel is on its way, so the row's Modify waits, as the cards' does. */
  held: (orderid: string) => boolean
  /** L4: "Cancel sent" (or what to check first), beside the row's status, in the cards' words. */
  mark: (orderid: string) => ReactNode
} {
  const sandbox = useSandbox()
  const marks = useSentMarks()
  /** An order's id while its Cancel asks, or `all` while Cancel all does. */
  const [asking, setAsking] = useState<string | null>(null)

  // Each book tells the cancels sent in the book it is (a mode switch alone changes nothing).
  const bookMode = useRef({ orders, sandbox })
  useEffect(() => {
    if (bookMode.current.orders !== orders) bookMode.current = { orders, sandbox }
    if (loading) return
    noteCancelBook(bookMode.current.sandbox, orders)
  }, [orders, loading, sandbox])

  const working = orders.filter((order) => orderState(order.order_status).working) as BookOrder[]
  const cancellable = working.filter((order) => !isHeld(cancelMark(marks, sandbox, order.orderid)))
  // Final, or gone from the book, while it asked: nothing left to cancel, so it closes.
  const asked =
    asking === null || asking === 'all'
      ? null
      : (working.find((order) => order.orderid === asking) ?? null)
  useEffect(() => {
    if (asking !== null && asking !== 'all' && asked === null && !loading) setAsking(null)
  }, [asking, asked, loading])
  const shown = useHeld(asked)
  const earlier = cancelWarning(shown ? cancelMark(marks, sandbox, shown.orderid) : undefined)
  const allEarlier = cancelWarning(
    marksOf(marks, 'cancel', sandbox)
      .filter((mark) => mark.expired && cancellable.some((order) => order.orderid === mark.id))
      .sort((a, b) => b.sentAt - a.sentAt)[0]
  )
  const close = () => setAsking(null)
  const heldWords = (orderid: string) => cancelWords(cancelMark(marks, sandbox, orderid))

  return {
    held: (orderid) => heldWords(orderid) !== null,
    mark: (orderid) => {
      const words = heldWords(orderid)
      if (!words) return null
      return (
        <>
          {words.chip && <SentChip label={words.chip} />}
          {words.note && <SentNote text={words.note} />}
        </>
      )
    },
    ask: (order) => {
      const mark = cancelMark(sentMarksNow(), sandbox, order.orderid)
      if (isHeld(mark)) return void showToast.warning(cancelHeldWhy(mark), 'orders')
      setAsking(order.orderid)
    },
    askAll: () => {
      if (cancellable.length > 0) return setAsking('all')
      showToast.warning(working.length > 0 ? EVERY_CANCEL_SENT : NOTHING_TO_CANCEL, 'orders')
    },
    dialog: (
      <>
        <Confirm
          open={asked !== null}
          title="Cancel this order?"
          description={
            shown ? (
              <>
                {earlier && <WarningLine text={earlier} />}
                {cancelSentence(shown, sandbox)}
              </>
            ) : (
              ''
            )
          }
          cancelLabel="Keep order"
          confirmLabel="Cancel order"
          onConfirm={() => {
            if (!asked) return
            const { orderid } = asked
            const mark = cancelMark(sentMarksNow(), sandbox, orderid)
            // Held since this opened: nothing is sent, and the toast says why.
            if (isHeld(mark)) return void showToast.warning(cancelHeldWhy(mark), 'orders')
            void sendHeld('cancel', sandbox, [{ id: orderid }], () => onCancel(orderid))
          }}
          onClose={close}
        />
        <Confirm
          open={asking === 'all'}
          title="Cancel all open orders?"
          description={
            <>
              {allEarlier && <WarningLine text={allEarlier} />}
              {cancelAllSentence(cancellable.length, sandbox)}
            </>
          }
          cancelLabel="Keep orders"
          confirmLabel="Cancel all"
          onConfirm={() =>
            void sendHeld(
              'cancel',
              sandbox,
              cancellable.map((order) => ({ id: order.orderid })),
              () => onCancelAll()
            )
          }
          onClose={close}
        />
      </>
    ),
  }
}
