// SUDARSHAN P7 F7 (M7.32): Risk settings (design p7 9.3; owner decision 12): the risk-free rate,
// the limits for one holding and one sector, monthly expenses and the months the emergency fund
// should cover. Saved from the version read; the service's sentence shows when a value is refused.
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { getSettings, PortfolioError, saveSettings } from '../api'
import { Box } from '../sheets/EntryFields'
import { parseRupees, parseWhole, rupeesInput, scaledText } from '../sheets/parse'
import { PRIMARY, ProblemLine } from '../sheets/TabFields'
import { useSend } from '../sheets/useSend'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import type { OwnerSettings } from '../types'

const KEY = [...PORTFOLIO_KEY, 'risk', 'settings'] as const

/** "6.5" percent a year as 650 basis points; null unless it is a number of up to two decimals. */
export function rateToBp(text: string): number | null {
  return parseRupees(text)
}

/** What the form holds as the values the service takes; a text that is no number goes as null. */
export function valuesOf(form: Record<string, string>) {
  return {
    risk_free_bp: rateToBp(form.rate ?? ''),
    max_holding_pct: (form.holding ?? '').trim(),
    max_sector_pct: (form.sector ?? '').trim(),
    monthly_expenses_paise: (form.expenses ?? '').trim() === '' ? null : parseRupees(form.expenses),
    months_target: parseWhole(form.months ?? ''),
  } as unknown as Partial<Omit<OwnerSettings, 'version'>>
}

const trim = (pct: string) => String(Number(pct))

export function formOf(s: OwnerSettings): Record<string, string> {
  return {
    rate: scaledText(s.risk_free_bp, 100),
    holding: trim(s.max_holding_pct),
    sector: trim(s.max_sector_pct),
    expenses: s.monthly_expenses_paise === null ? '' : rupeesInput(s.monthly_expenses_paise),
    months: String(s.months_target),
  }
}

export function SettingsSheet({
  open,
  onClose,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Record<string, string>>({})
  const send = useSend()
  const query = useQuery({
    queryKey: KEY,
    queryFn: async ({ signal }) => {
      const result = await getSettings(signal)
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
    gcTime: 0,
  })
  const kept = query.data
  useEffect(() => {
    if (open && kept) setForm(formOf(kept))
  }, [open, kept])
  const set = (name: string) => (value: string) => setForm((was) => ({ ...was, [name]: value }))
  const save = async () => {
    if (!kept) return
    if (await send.run(() => saveSettings({ values: valuesOf(form), version: kept.version })))
      onSaved()
  }
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Risk settings"
      description="Your own assumptions and limits"
    >
      {query.error ? (
        <ProblemLine problem={query.error as PortfolioError} />
      ) : kept === undefined ? (
        <p className="py-4 text-sm text-[color:var(--text-secondary)]">Reading your settings.</p>
      ) : (
        <div className="flex flex-col gap-3">
          <Box
            label="Risk-free rate, percent a year"
            value={form.rate ?? ''}
            onChange={set('rate')}
            hint="What a safe deposit pays. Sharpe and Sortino measure the return above it."
          />
          <Box
            label="Flag one holding above, percent"
            value={form.holding ?? ''}
            onChange={set('holding')}
          />
          <Box
            label="Flag one sector above, percent"
            value={form.sector ?? ''}
            onChange={set('sector')}
          />
          <Box
            label="Monthly expenses, in rupees"
            value={form.expenses ?? ''}
            onChange={set('expenses')}
            hint="For the emergency fund. Leave empty to leave it out."
          />
          <Box
            label="Emergency fund target, months"
            value={form.months ?? ''}
            onChange={set('months')}
            inputMode="numeric"
          />
          <ProblemLine problem={send.problem} />
          <button
            type="button"
            className={PRIMARY}
            disabled={send.pending}
            onClick={() => void save()}
          >
            {send.pending ? 'Saving' : 'Save'}
          </button>
        </div>
      )}
    </BottomSheet>
  )
}
