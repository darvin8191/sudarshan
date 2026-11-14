import { Calculator, ChevronDown } from 'lucide-react'
import { useId, useState } from 'react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { formatPrice, formatRupees } from '@/sudarshan/dashboard/format'
import { priceDecimals } from '@/sudarshan/watchlist/rows'
import { readRisk, rememberRisk, type Side, sizeByRisk } from './sizeHelper'

/**
 * The order ticket's "Size by risk" row (B4: M5.11), folded until it is opened: a sum to risk
 * and a stop price give whole lots (shares in cash) between the order's price and the stop, and
 * "Set quantity" puts that size in the ticket's quantity box. It changes no price and sends
 * nothing: the owner still presses the ticket's own button. The sum is kept per segment on this
 * phone (sizeHelper.ts).
 */
export function SizeByRisk({
  exchange,
  side,
  lotSize,
  entry,
  onFill,
}: {
  exchange: string
  side: Side
  /** The contract's lot; one where the ticket counts in shares. */
  lotSize: number
  /** The price the order would fill near (sizeHelper's entryOf), or null while not known. */
  entry: number | null
  /** Puts this many units in the ticket's quantity box. */
  onFill: (units: number) => void
}) {
  const [open, setOpen] = useState(false)
  const [risk, setRisk] = useState(() => String(readRisk(exchange) ?? ''))
  const [stop, setStop] = useState('')
  const id = useId()
  const sum = numberOf(risk)
  const answer = sizeByRisk({ risk: sum, stop: numberOf(stop), entry, side, lotSize })
  const inLots = lotSize > 1
  const keep = () => {
    if (sum !== null) rememberRisk(exchange, sum)
  }

  return (
    <section aria-label="Size by risk" className="rounded-lg border">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((was) => !was)}
        className="flex min-h-12 w-full items-center justify-between gap-2 rounded-lg px-3 text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="inline-flex items-center gap-2">
          <Calculator aria-hidden="true" className="size-4" />
          Size by risk
        </span>
        <ChevronDown aria-hidden="true" className={cn('size-4', open && 'rotate-180')} />
      </button>
      {open && (
        <div id={id} className="flex flex-col gap-2 border-t px-3 pt-2 pb-3">
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-xs font-medium">
              Risk (₹)
              <Input
                type="number"
                inputMode="decimal"
                min={0}
                value={risk}
                onChange={(event) => setRisk(event.target.value)}
                onBlur={keep}
                className="h-12 text-base"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium">
              Stop price
              <Input
                type="number"
                inputMode="decimal"
                min={0}
                value={stop}
                onChange={(event) => setStop(event.target.value)}
                className="h-12 text-base"
              />
            </label>
          </div>
          {answer?.kind === 'why' && (
            <p className="text-xs font-medium text-[color:var(--warning-text)]">{answer.text}</p>
          )}
          {answer?.kind === 'size' && (
            <>
              <p data-testid="risk-size" className="text-sm">
                <span className="font-bold">
                  {inLots
                    ? `${answer.lots} ${answer.lots === 1 ? 'lot' : 'lots'} (${answer.units})`
                    : `${answer.units} ${answer.units === 1 ? 'share' : 'shares'}`}
                </span>
                {` ${answer.lots === 1 ? 'risks' : 'risk'} ${formatRupees(answer.atRisk)}, ${formatPrice(answer.perUnit, priceDecimals(exchange))} a unit to the stop`}
              </p>
              <button
                type="button"
                onClick={() => {
                  keep()
                  onFill(answer.units)
                }}
                className="min-h-12 rounded-md border border-primary px-3 text-sm font-semibold text-primary outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Set quantity to{' '}
                {inLots ? `${answer.lots} ${answer.lots === 1 ? 'lot' : 'lots'}` : answer.units}
              </button>
            </>
          )}
        </div>
      )}
    </section>
  )
}

/** A typed figure, or null when the box holds none. */
function numberOf(text: string): number | null {
  if (text.trim() === '') return null
  const value = Number(text)
  return Number.isFinite(value) ? value : null
}
