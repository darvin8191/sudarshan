// SUDARSHAN M7.39: the price sheet (design 4.5 and 4.6 widget 9). One value the owner keeps by
// hand, on a day: a manual asset's value per unit, a fund's NAV when AMFI's is old, or a loan's
// outstanding balance (0 once it is paid off). The service keeps one value per day; the last one
// entered for a day stands. Nothing here places or changes an order.
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { PortfolioError, portfolioCall } from '../api'
import { dayOfInstant } from '../days'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import type { AssetKind } from '../types'
import { Box } from './EntryFields'
import { dayToInput, inputToDay, parsePrice, parseRupees } from './parse'
import { PRIMARY, ProblemLine, SECONDARY } from './TabFields'
import { useSend } from './useSend'

/** What the sheet prices: enough of an asset to name it and to word the field. */
export interface PriceTarget {
  asset_id: number
  name: string
  kind: AssetKind
  /** A manual asset's unit ("g", "unit"); null for a fund or a loan. */
  unit_label: string | null
  /** The tab the sheet was opened from: a loan set from it is linked to it. */
  tab_id: number
}

/** The field's words, by what is being set. */
export function fieldWords(target: PriceTarget): { label: string; hint: string } {
  if (target.kind === 'liability')
    return {
      label: 'Outstanding balance (₹)',
      hint: 'What is still owed on this day. Enter 0 once the loan is paid off.',
    }
  if (target.kind === 'mf')
    return {
      label: 'NAV (₹ per unit)',
      hint: 'As the fund house or AMFI shows it. The published NAV takes over when a newer one arrives.',
    }
  const unit = target.unit_label ? ` per ${target.unit_label}` : ''
  return {
    label: `Value (₹${unit})`,
    hint: 'Up to four decimals. The newest value on or before a day prices the holding.',
  }
}

/** The text typed, as the service's price_u (rupees x 10^4), or null when it is not a number. */
export function priceOf(target: PriceTarget, text: string): number | null {
  if (target.kind === 'liability') {
    const paise = parseRupees(text)
    return paise === null ? null : paise * 100
  }
  const price = parsePrice(text)
  return price === null || price === 0 ? null : price
}

export function PriceSheet({
  open,
  onClose,
  target,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  target: PriceTarget | null
  onSaved?: () => void
}) {
  const today = dayOfInstant(Date.now())
  const [value, setValue] = useState('')
  const [date, setDate] = useState(dayToInput(today))
  const send = useSend()
  const queries = useQueryClient()

  // biome-ignore lint/correctness/useExhaustiveDependencies: a fresh form on each opening only
  useEffect(() => {
    if (!open) return
    setValue('')
    setDate(dayToInput(dayOfInstant(Date.now())))
    send.clear()
  }, [open, target?.asset_id])

  if (!target) return null
  const words = fieldWords(target)
  const day = inputToDay(date)
  const price = priceOf(target, value)
  const future = day !== null && day > today
  const ready = day !== null && !future && price !== null && !send.pending

  const save = async () => {
    if (!ready || day === null || price === null) return
    const ok = await send.run(async () => {
      const result = await portfolioCall('price_point_set', {
        id: target.asset_id,
        day,
        price_u: price,
        ...(target.kind === 'liability' ? { tab_id: target.tab_id } : {}),
      })
      if (!result.enabled)
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
    })
    if (!ok) return
    void queries.invalidateQueries({ queryKey: PORTFOLIO_KEY })
    onSaved?.()
    onClose()
  }

  const title =
    target.kind === 'liability'
      ? 'Update the balance'
      : target.kind === 'mf'
        ? 'Enter the NAV'
        : 'Update the value'
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={title}
      description={target.name}
    >
      <form
        className="flex flex-col gap-4 overflow-y-auto px-4 pb-4"
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
      >
        <Box label={words.label} value={value} onChange={setValue} disabled={send.pending} />
        <p className="-mt-2 text-xs text-[color:var(--text-tertiary)]">{words.hint}</p>
        <Box label="On" type="date" value={date} onChange={setDate} disabled={send.pending} />
        {future && (
          <p className="text-sm font-medium text-[color:var(--warning-text)]">
            A value cannot be dated after today.
          </p>
        )}
        <ProblemLine problem={send.problem} />
        <div className="flex gap-2">
          <button type="button" className={`${SECONDARY} flex-1`} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={`${PRIMARY} flex-1`} disabled={!ready}>
            {send.pending ? 'Saving' : 'Save'}
          </button>
        </div>
      </form>
    </BottomSheet>
  )
}
