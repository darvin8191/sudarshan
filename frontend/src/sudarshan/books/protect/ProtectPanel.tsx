import { Pencil, Shield, ShieldCheck, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  ActionWords,
  Confirm,
  OUTLINED,
  price,
  SentChip,
  useSandbox,
} from '@/sudarshan/books/cards/parts'
import { gttMark, isHeld, sendHeld, useSentMarks } from '@/sudarshan/books/sent'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import type { Holding } from '@/types/trading'
import {
  asks,
  checkQuantity,
  checkTrigger,
  LINES,
  NOT_READ,
  PLACING,
  type Protect,
  protectable,
  protectedLine,
  refusal,
  sellQuestion,
} from './protect'
import { changeProtect, placeProtect, removeProtect } from './send'
import type { ProtectBook } from './useProtect'

/** The hold of a Protect being placed, by its holding: one send, however it is tapped. */
export const placingId = (holding: Pick<Holding, 'exchange' | 'symbol'>) =>
  `${PLACING}${holding.exchange}:${holding.symbol}`

const number = (text: string): number | null =>
  /^\d+(\.\d+)?$/.test(text.trim()) ? Number(text) : null

/**
 * A holding's Protect on its sheet (M5.6): what is working on it, as the GTT book says, with
 * Change and Remove; or the button that sets one (G2, ORDER PATH behind SUDARSHAN_GTT_PROTECT).
 * Setting, changing and removing each ask first, send once and stay "sent" (books/sent.ts) until
 * the GTT book settles them. Never colour alone: a shield and the words.
 */
export function ProtectPanel({
  holding,
  last,
  book,
}: {
  holding: Holding
  /** The holding's last price. */
  last: number | null
  book: ProtectBook
}) {
  const sandbox = useSandbox()
  const marks = useSentMarks()
  const [sheet, setSheet] = useState<null | 'new' | Protect>(null)
  const [removing, setRemoving] = useState<Protect | null>(null)
  if (!protectable(holding)) return null
  const protects = book.of(holding)
  const placing = isHeld(gttMark(marks, sandbox, placingId(holding)))
  const blocked = refusal(holding, protects)

  const remove = (protect: Protect) =>
    void sendHeld('gtt', sandbox, [{ id: protect.triggerId }], async () => {
      const outcome = await removeProtect(protect.triggerId)
      void book.refresh()
      return outcome
    })

  return (
    <section aria-label="Protect" className="flex flex-col gap-2">
      {protects.map((protect) => {
        const held = isHeld(gttMark(marks, sandbox, protect.triggerId))
        return (
          <div key={protect.triggerId} className="flex flex-col gap-2">
            <p
              data-protect="true"
              className="flex items-center gap-2 text-[0.9375rem] font-semibold text-[color:var(--info)]"
            >
              <ShieldCheck aria-hidden="true" className="size-5 shrink-0" />
              {protectedLine(protect)}
              {held && <SentChip label="Sent" />}
            </p>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                aria-haspopup="dialog"
                disabled={held}
                onClick={() => setSheet(protect)}
                className={OUTLINED}
              >
                <Pencil aria-hidden="true" className="size-4" />
                Change
              </button>
              <button
                type="button"
                aria-haspopup="dialog"
                disabled={held}
                onClick={() => setRemoving(protect)}
                className={OUTLINED}
              >
                <X aria-hidden="true" className="size-4" />
                Remove
              </button>
            </div>
          </div>
        )
      })}
      {protects.length === 0 && (
        <button
          type="button"
          aria-haspopup="dialog"
          disabled={placing || !book.read}
          onClick={() => setSheet('new')}
          className={cn(OUTLINED, 'w-full')}
        >
          <Shield aria-hidden="true" className="size-5" />
          <ActionWords sandbox={sandbox}>{placing ? 'Protect sent' : 'Protect'}</ActionWords>
        </button>
      )}
      {book.failed && (
        <p className="text-[0.8125rem] font-medium text-[color:var(--warning-text)]">{NOT_READ}</p>
      )}
      {sheet !== null && (
        <ProtectSheet
          holding={holding}
          last={last}
          editing={sheet === 'new' ? null : sheet}
          blocked={sheet === 'new' ? blocked : null}
          book={book}
          onClose={() => setSheet(null)}
        />
      )}
      <Confirm
        open={removing !== null}
        title="Remove Protect?"
        description={
          removing
            ? `${protectedLine(removing).replace('Protected: sell', 'The order to sell')} is cancelled. ${holding.symbol} is then not protected.`
            : ''
        }
        cancelLabel="Keep it"
        confirmLabel="Remove"
        onConfirm={() => {
          if (removing) remove(removing)
          setRemoving(null)
        }}
        onClose={() => setRemoving(null)}
      />
    </section>
  )
}

/** Set a Protect, or change one: the price, how many, what Upstox will do, then one question. */
function ProtectSheet({
  holding,
  last,
  editing,
  blocked,
  book,
  onClose,
}: {
  holding: Holding
  last: number | null
  editing: Protect | null
  blocked: string | null
  book: ProtectBook
  onClose: () => void
}) {
  const sandbox = useSandbox()
  const held = Number(holding.quantity) || 0
  const [trigger, setTrigger] = useState(editing ? String(editing.trigger) : '')
  const [quantity, setQuantity] = useState(String(editing ? editing.quantity : held))
  const [asking, setAsking] = useState(false)
  const once = useRef(false)
  useEffect(() => {
    once.current = false
  }, [])

  const check = checkTrigger(number(trigger), last)
  const quantityProblem = checkQuantity(number(quantity), held)
  const problem =
    blocked ?? (trigger.trim() === '' ? null : !check.ok ? check.sentence : quantityProblem)
  const ready = blocked === null && check.ok && quantityProblem === null

  const send = () => {
    if (once.current || !check.ok || quantityProblem !== null || blocked !== null) return
    once.current = true
    const shares = number(quantity) as number
    const at = number(trigger) as number
    const side = check.side
    onClose()
    const id = editing ? editing.triggerId : placingId(holding)
    void sendHeld('gtt', sandbox, [{ id }], async () => {
      // One per holding: the book is read again just before a new one goes.
      const outcome = editing
        ? await changeProtect(editing.triggerId, holding, shares, at, side)
        : await placeProtect(holding, shares, at, side, book)
      void book.refresh()
      return outcome
    })
  }

  return (
    <BottomSheet
      open
      onOpenChange={(next) => !next && onClose()}
      title={editing ? `Change Protect: ${holding.symbol}` : `Protect ${holding.symbol}`}
      description={`You hold ${held} · Last ${price(last, holding.exchange)}`}
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          if (ready) setAsking(true)
        }}
      >
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1.5 text-[0.9375rem] font-semibold">
            Sell at price
            <Input
              className="h-12 text-base"
              inputMode="decimal"
              value={trigger}
              onChange={(e) => setTrigger(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1.5 text-[0.9375rem] font-semibold">
            Shares
            <Input
              className="h-12 text-base"
              inputMode="numeric"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
            />
          </label>
        </div>
        {check.ok && (
          <p data-testid="protect-distance" className="text-[0.9375rem] font-semibold">
            Sells if the price {check.side} to {price(number(trigger), holding.exchange)}:{' '}
            {check.distance < 0 ? '-' : '+'}
            {price(Math.abs(check.distance), holding.exchange)} ({check.percent < 0 ? '-' : '+'}
            {Math.abs(check.percent).toFixed(2)}%) from the last price
          </p>
        )}
        {problem && (
          <p
            role="alert"
            className="text-[0.9375rem] font-semibold text-[color:var(--warning-text)]"
          >
            {problem}
          </p>
        )}
        <ul className="flex list-disc flex-col gap-1 pl-5 text-[0.875rem] text-[color:var(--text-secondary)]">
          {LINES.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <button type="submit" disabled={!ready} className={cn(OUTLINED, 'w-full font-bold')}>
          <ActionWords sandbox={sandbox}>{editing ? 'Change Protect' : 'Set Protect'}</ActionWords>
        </button>
      </form>
      <Confirm
        open={asking}
        title={editing ? 'Change Protect?' : 'Set Protect?'}
        description={
          check.ok
            ? asks(holding.symbol, number(quantity) ?? 0, number(trigger) ?? 0, check.side)
            : ''
        }
        confirmLabel={editing ? 'Change' : 'Set Protect'}
        confirmClassName="bg-primary text-primary-foreground hover:bg-primary/90"
        onConfirm={send}
        onClose={() => setAsking(false)}
      />
    </BottomSheet>
  )
}

/**
 * Sell on a holding that has a Protect: the question first. With "Remove it too" ticked, as it
 * starts, the Protect is removed and then the page's own ticket opens; a Protect left in place
 * would still try to sell these shares later.
 */
export function SellQuestion({
  holding,
  book,
  onSell,
  onClose,
}: {
  holding: Holding | null
  book: ProtectBook
  onSell: () => void
  onClose: () => void
}) {
  const sandbox = useSandbox()
  const [remove, setRemove] = useState(true)
  const protects = holding ? book.of(holding) : []
  // biome-ignore lint/correctness/useExhaustiveDependencies: ticked again for each holding asked about
  useEffect(() => setRemove(true), [holding?.symbol, holding?.exchange])
  return (
    <Confirm
      open={holding !== null && protects.length > 0}
      title={holding ? `Sell ${holding.symbol}?` : ''}
      description={
        <span className="flex flex-col gap-3">
          <span>{protects[0] ? sellQuestion(protects[0]) : ''}</span>
          <label className="flex min-h-12 items-center gap-3 font-semibold text-foreground">
            <input
              type="checkbox"
              className="size-5"
              checked={remove}
              onChange={(e) => setRemove(e.target.checked)}
            />
            Remove Protect now
          </label>
          <span>
            {remove
              ? 'Protect is removed before the order ticket opens. If you then do not sell, set it again.'
              : 'Protect stays. If its price is reached later it will try to sell these shares again.'}
          </span>
        </span>
      }
      confirmLabel="Continue to sell"
      onConfirm={() => {
        if (remove) {
          for (const protect of protects) {
            void sendHeld('gtt', sandbox, [{ id: protect.triggerId }], async () => {
              const outcome = await removeProtect(protect.triggerId)
              void book.refresh()
              return outcome
            })
          }
        }
        onSell()
      }}
      onClose={onClose}
    />
  )
}
