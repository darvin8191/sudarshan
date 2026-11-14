// SUDARSHAN P7 F3 (M14.2): the parts of a trade's actual charges on the trade sheet (design p7
// 9.7). The row above says the total and that it is actual; a tap here opens what Upstox charged
// part by part, as the journal shared the day's figures among the day's orders. Every figure is
// the service's; nothing is added up here.
import { ChevronDown } from 'lucide-react'
import { useId, useState } from 'react'
import { cn } from '@/lib/utils'
import { paiseAmount } from './money'
import type { ChargeActual } from './types'

/** A part below zero (Upstox gave something back) keeps its sign as a true minus. */
function partAmount(paise: number): string {
  return paise < 0 ? `−${paiseAmount(-paise)}` : paiseAmount(paise)
}

export function partialWords(missing: readonly string[]): string {
  return missing.length === 0
    ? 'Upstox left a part of these charges empty, so this total may be short.'
    : `Upstox left these empty, so this total may be short: ${missing.join(', ')}.`
}

export function ChargesParts({
  actual,
  estimate,
}: {
  actual: ChargeActual
  estimate: number | null
}) {
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <div className="flex flex-col gap-1">
      <dt className="sr-only">Charges, part by part</dt>
      <dd className="flex flex-col gap-1">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen((was) => !was)}
          className="inline-flex min-h-12 items-center justify-end gap-1.5 self-end rounded-[0.875rem] px-2 text-sm font-semibold text-[color:var(--brand)] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          {open ? 'Hide the parts' : 'Show the parts'}
          <ChevronDown aria-hidden="true" className={cn('size-4', open && 'rotate-180')} />
        </button>
        {open && (
          <div
            id={id}
            className="flex flex-col gap-1 rounded-[0.875rem] border border-[color:var(--divider)] px-3 py-2"
          >
            <ul aria-label="Charges, part by part" className="flex flex-col gap-1 tabular-nums">
              {actual.parts.map((part) => (
                <li key={part.part} className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="text-[color:var(--text-secondary)]">{part.label}</span>
                  <span className="font-semibold">{partAmount(part.paise)}</span>
                </li>
              ))}
            </ul>
            {actual.partial && (
              <p className="text-xs font-semibold text-[color:var(--warning-text)]">
                {partialWords(actual.missing)}
              </p>
            )}
            {estimate !== null && (
              <p className="text-xs text-[color:var(--text-tertiary)]">
                The journal’s estimate was {paiseAmount(estimate)}.
              </p>
            )}
          </div>
        )}
      </dd>
    </div>
  )
}
