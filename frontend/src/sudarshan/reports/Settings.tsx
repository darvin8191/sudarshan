import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, LoaderCircle, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { journalData, problemOf, WORDS } from '../journal/api'
import { Problem } from '../journal/parts'
import { parseRupees, riskText } from '../journal/sheetForm'
import type { SettingsSetReq } from '../journal/types'
import { REPORTS_KEY, useReportSettings } from './queries'
import {
  checkWords,
  kindTitle,
  periodWords,
  ruleSentence,
  SWITCHES,
  TIME_NOT_SAID,
  timeOf,
} from './words'

const CARD = 'rounded-[1.125rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]'
const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60'

const GROUPED = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 })

/** The capital as the field starts from it, in en-IN digits (review L2): 5,00,000 or 1,250.50. */
export function capitalText(paise: number | null): string {
  if (paise === null) return ''
  const text = riskText(paise)
  return text.includes('.')
    ? new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2 }).format(paise / 100)
    : GROUPED.format(paise / 100)
}

export const CAPITAL_WORDS =
  'Capital is the one figure used for percentage returns, beta and the drawdown percentage. It is not linked to the daily loss limit on the Risk page.'

function rateCardWords(
  card: { version?: string | null; from_day?: number | null } | null | undefined
): string {
  if (!card || (!card.version && !card.from_day))
    return 'The server has not said which rate card is in use yet.'
  const when = card.from_day ? `, in force from ${periodWords(String(card.from_day))}` : ''
  return `${card.version ?? 'Rate card'}${when}`
}

export function Settings() {
  const query = useReportSettings()
  const queryClient = useQueryClient()
  const settings = query.data
  const [capital, setCapital] = useState('')
  const seen = useRef<{ version: number; text: string } | null>(null)
  useEffect(() => {
    if (!settings || seen.current?.version === settings.version) return
    const text = capitalText(settings.capital_base_paise)
    const last = seen.current
    // Review L2: a capital being typed is not reset when a switch saved meanwhile moves the
    // version; only a field still showing what was read last takes the new figure.
    setCapital((now) => (last === null || now === last.text ? text : now))
    seen.current = { version: settings.version, text }
  }, [settings])

  const save = useMutation({
    mutationFn: async (change: Omit<SettingsSetReq, 'version'>) => {
      if (!settings) throw new Error('not read')
      return journalData('settings_set', { ...change, version: settings.version })
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: [...REPORTS_KEY, 'settings'] }),
  })

  if (query.isPending) {
    return (
      <p className="flex items-center gap-2 py-6 text-base font-bold" aria-live="polite">
        <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
        Loading the settings
      </p>
    )
  }
  if (query.isError || !settings) {
    return (
      <div className="flex flex-col gap-3">
        <Problem>{problemOf(query.error).message}</Problem>
        <button type="button" className={BUTTON} onClick={() => query.refetch()}>
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </div>
    )
  }

  const parsed = parseRupees(capital)
  const capitalProblem = parsed.valid
    ? null
    : 'Capital is a rupee amount above zero, such as 5,00,000. Correct it or clear it.'
  const changed = parsed.valid && parsed.paise !== settings.capital_base_paise
  const failed = save.isError
    ? save.error instanceof Error && save.error.message === 'not read'
      ? WORDS.unreadable
      : problemOf(save.error).message
    : null

  return (
    <div className="flex flex-col gap-3">
      {failed && <Problem>{failed}</Problem>}
      <section aria-label="Reports" className={`${CARD} flex flex-col divide-y`}>
        {SWITCHES.map((row) => {
          const on = settings[row.key]
          const time = timeOf(settings.times, row.at)
          const check = row.kind === 'eod' ? checkWords(settings.times) : null
          return (
            <div key={row.key} className="flex items-center justify-between gap-3 py-2">
              <div className="min-w-0">
                <p className="text-base font-bold">{kindTitle(row.kind)}</p>
                <p className="text-sm text-[color:var(--text-secondary)]" data-testid="when">
                  {time ? (
                    <>
                      <span className="font-semibold [font-variant-numeric:tabular-nums]">
                        {time}
                      </span>
                      {', '}
                      {row.rule}
                    </>
                  ) : (
                    `${ruleSentence(row.rule)} ${TIME_NOT_SAID}`
                  )}
                </p>
                {check && (
                  <p className="text-sm text-[color:var(--text-secondary)] [font-variant-numeric:tabular-nums]">
                    {check}
                  </p>
                )}
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={on}
                aria-label={`${kindTitle(row.kind)}`}
                disabled={save.isPending}
                onClick={() => save.mutate({ [row.key]: !on })}
                className="inline-flex min-h-12 min-w-[5.5rem] shrink-0 items-center justify-center gap-1.5 rounded-[0.875rem] border border-input px-3 text-[0.9375rem] font-bold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60 aria-checked:border-[color:var(--brand)] aria-checked:bg-[color:var(--brand)] aria-checked:text-white"
              >
                {on && <Check aria-hidden="true" className="size-4" />}
                {on ? 'On' : 'Off'}
              </button>
            </div>
          )
        })}
      </section>

      <section aria-label="Capital" className={`${CARD} flex flex-col gap-2`}>
        <label htmlFor="capital" className="text-base font-bold">
          Trading capital (₹)
        </label>
        <input
          id="capital"
          inputMode="decimal"
          autoComplete="off"
          value={capital}
          onChange={(event) => setCapital(event.target.value)}
          aria-invalid={capitalProblem !== null}
          aria-describedby="capital-note"
          className="min-h-12 rounded-[0.875rem] border border-input bg-background px-3 text-base [font-variant-numeric:tabular-nums]"
        />
        <p id="capital-note" className="text-sm text-[color:var(--text-secondary)]">
          {capitalProblem ?? CAPITAL_WORDS}
        </p>
        <button
          type="button"
          className={BUTTON}
          disabled={!changed || save.isPending}
          onClick={() => save.mutate({ capital_base_paise: parsed.paise })}
        >
          {save.isPending ? 'Saving' : changed ? 'Save capital' : 'No changes to save'}
        </button>
      </section>

      <section aria-label="Language and rate card" className={`${CARD} flex flex-col gap-2`}>
        <div>
          <p className="text-base font-bold">Report language</p>
          <p className="text-sm text-[color:var(--text-secondary)]">English only for now.</p>
        </div>
        <div>
          <p className="text-base font-bold">Rate card in use</p>
          <p className="text-sm text-[color:var(--text-secondary)]">
            {rateCardWords(settings.rate_card)}
          </p>
        </div>
      </section>
    </div>
  )
}
