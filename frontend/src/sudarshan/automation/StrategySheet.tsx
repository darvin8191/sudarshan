// SUDARSHAN M12.1: New strategy and Edit. The server reads the form and answers a refusal in a
// sentence (contract.clean), so nothing here decides what a strategy may be. A strategy is born
// in the Sandbox and Stopped; while it holds a position only its name and limits can change.
import { useQueryClient } from '@tanstack/react-query'
import { Loader2, TriangleAlert } from 'lucide-react'
import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Segmented } from '@/sudarshan/journal/Segmented'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { automationApi, LIST_KEY } from './api'
import type { CreateAnswer, Direction, Product, SizeMode, Strategy, StrategyForm } from './types'

interface Draft {
  name: string
  kind: 'future' | 'option' | 'same'
  chart: string
  underlying: string
  exchange: string
  expiry_rule: string
  strike_offset: string
  product: Product
  direction: Direction
  size: string
  size_mode: SizeMode
  multiplier: string
  max_lots: string
  max_age_s: string
  max_alerts_day: string
}

const BLANK: Draft = {
  name: '',
  kind: 'future',
  chart: '',
  underlying: 'NIFTY',
  exchange: 'NFO',
  expiry_rule: 'month',
  strike_offset: 'ATM',
  product: 'MIS',
  direction: 'both',
  size: '1',
  size_mode: 'fixed',
  multiplier: '',
  max_lots: '',
  max_age_s: '30',
  max_alerts_day: '20',
}

function draftOf(strategy: Strategy): Draft {
  return {
    name: strategy.name,
    kind: strategy.kind,
    chart: strategy.chart ?? '',
    underlying: strategy.underlying,
    exchange: strategy.exchange,
    expiry_rule: strategy.expiry_rule ?? 'month',
    strike_offset: strategy.strike_offset ?? 'ATM',
    product: strategy.product,
    direction: strategy.direction,
    size: String(strategy.lots ?? strategy.quantity ?? ''),
    size_mode: strategy.kind === 'option' ? 'fixed' : strategy.size_mode,
    multiplier: strategy.multiplier == null ? '' : String(strategy.multiplier),
    max_lots: strategy.max_lots == null ? '' : String(strategy.max_lots),
    max_age_s: String(strategy.max_age_s),
    max_alerts_day: String(strategy.max_alerts_day),
  }
}

/** A chart of a future (NIFTY1!) or of one option is sized in lots; a share in shares. */
export const inLots = (draft: Pick<Draft, 'kind' | 'chart'>): boolean =>
  draft.kind !== 'same' || /[12]!$|\d{6}[CP]\d/.test(draft.chart.trim().toUpperCase())

/** A whole number as typed, or null: the server's sentence then says what the field takes. */
const whole = (text: string): number | null => (/^\d+$/.test(text.trim()) ? Number(text) : null)

/** A number as typed, or null. */
const numberOf = (text: string): number | null =>
  /^\d+(\.\d+)?$/.test(text.trim()) ? Number(text) : null

/** An option strategy always takes a fixed size. */
const scaledOf = (draft: Pick<Draft, 'kind' | 'size_mode'>): boolean =>
  draft.size_mode === 'scaled' && draft.kind !== 'option'

export function formOf(draft: Draft): StrategyForm {
  const lots = inLots(draft)
  const allowed: Product[] = lots ? ['MIS', 'NRML'] : ['MIS', 'CNC']
  return {
    name: draft.name.trim(),
    kind: draft.kind,
    chart: draft.chart.trim().toUpperCase() || null,
    product: allowed.includes(draft.product) ? draft.product : 'MIS',
    direction: draft.direction,
    max_age_s: whole(draft.max_age_s) as number,
    max_alerts_day: whole(draft.max_alerts_day) as number,
    ...(draft.kind !== 'same' && {
      exchange: draft.exchange,
      underlying: draft.underlying.trim().toUpperCase(),
      expiry_rule: ruleOf(draft),
    }),
    ...(draft.kind === 'option' && { strike_offset: draft.strike_offset }),
    ...(scaledOf(draft)
      ? {
          size_mode: 'scaled' as const,
          multiplier: numberOf(draft.multiplier),
          max_lots: whole(draft.max_lots),
        }
      : lots
        ? { lots: whole(draft.size) }
        : { quantity: whole(draft.size) }),
  }
}

type Choices = ReadonlyArray<readonly [string, string]>
const FUTURE_RULES: Choices = [
  ['month', 'This month'],
  ['next_month', 'Next month'],
]
const OPTION_RULES: Choices = [
  ['nearest', 'Nearest expiry'],
  ['next', 'Next expiry'],
  ['month', 'This month'],
]
/** ATM, then 1 to 10 strikes in and out of the money, as the server reads them. */
const STRIKES: Choices = [
  ['ATM', 'At the money (ATM)'],
  ...Array.from({ length: 10 }, (_, n) => [`ITM${n + 1}`, `${n + 1} in the money`] as const),
  ...Array.from({ length: 10 }, (_, n) => [`OTM${n + 1}`, `${n + 1} out of the money`] as const),
]

/** The expiry rule the kind takes: a rule left over from the other kind reads as its first. */
const ruleOf = (draft: Pick<Draft, 'kind' | 'expiry_rule'>): string => {
  const rules = draft.kind === 'option' ? OPTION_RULES : FUTURE_RULES
  return rules.some(([key]) => key === draft.expiry_rule) ? draft.expiry_rule : rules[0][0]
}

const CONTROL =
  'h-12 w-full rounded-xl border border-[color:var(--input)] bg-card px-3 text-base outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60'

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: (id: string) => ReactNode
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[0.9375rem] font-semibold">
        {label}
      </label>
      {children(id)}
      {hint && <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">{hint}</p>}
    </div>
  )
}

function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: ReadonlyArray<readonly [T, string]>
  onChange: (value: T) => void
}) {
  return (
    <Field label={label}>
      {(id) => (
        <select
          id={id}
          className={CONTROL}
          value={value}
          onChange={(e) => onChange(e.target.value as T)}
        >
          {options.map(([key, word]) => (
            <option key={key} value={key}>
              {word}
            </option>
          ))}
        </select>
      )}
    </Field>
  )
}

export function StrategySheet({
  open,
  onClose,
  strategy,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  /** The strategy to edit; none for a new one. */
  strategy: Strategy | null
  onCreated: (answer: CreateAnswer) => void
}) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Draft>(BLANK)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const busy = useRef(false)
  const editing = strategy?.id ?? null
  // biome-ignore lint/correctness/useExhaustiveDependencies: the form starts again when the sheet opens or is for another strategy, not when a live event redraws the card
  useEffect(() => {
    if (!open) return
    setDraft(strategy ? draftOf(strategy) : BLANK)
    setRefusal(null)
  }, [open, editing])

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((old) => ({ ...old, [key]: value }))
  const held = Boolean(strategy?.held_symbol)
  const lots = inLots(draft)

  const save = async () => {
    if (busy.current) return
    busy.current = true
    setSending(true)
    setRefusal(null)
    try {
      const form = formOf(draft)
      if (!strategy) {
        const answer = await automationApi.create(form)
        if (!answer.ok) return setRefusal(answer.message)
        void queryClient.invalidateQueries({ queryKey: LIST_KEY })
        return onCreated(answer)
      }
      const before = formOf(draftOf(strategy)) as unknown as Record<string, unknown>
      const changes = Object.fromEntries(
        Object.entries(form).filter(([key, value]) => before[key] !== value)
      )
      if (Object.keys(changes).length === 0) return onClose()
      const answer = await automationApi.update(strategy.id, strategy.version, changes)
      if (!answer.ok) return setRefusal(answer.message)
      void queryClient.invalidateQueries({ queryKey: ['sudarshan-automation'] })
      onClose()
    } finally {
      busy.current = false
      setSending(false)
    }
  }

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && !sending && onClose()}
      title={strategy ? `Edit ${strategy.name}` : 'New strategy'}
      description={strategy ? strategy.trades : 'Starts in the Sandbox, Stopped'}
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
      >
        <Field label="Name" hint="As you will see it in alerts and in the journal.">
          {(id) => (
            <Input
              id={id}
              className="h-12 text-base"
              maxLength={40}
              value={draft.name}
              onChange={(e) => set('name', e.target.value)}
            />
          )}
        </Field>
        {held && (
          <p className="text-[0.875rem] font-semibold text-[color:var(--warning-text)]">
            {strategy?.name} holds a position, so only its name and limits can change. Close the
            position to change the rest.
          </p>
        )}
        <fieldset disabled={held} className="flex min-w-0 flex-col gap-4 disabled:opacity-60">
          <Segmented
            label="What it trades"
            value={draft.kind}
            options={[
              { id: 'future', label: 'A future' },
              { id: 'option', label: 'An option' },
              { id: 'same', label: 'Its chart' },
            ]}
            onChange={(kind) =>
              setDraft((old) => ({
                ...old,
                kind,
                expiry_rule: kind === 'option' ? 'nearest' : 'month',
              }))
            }
          />
          {draft.kind !== 'same' && (
            <>
              <Field label="Underlying" hint="Such as NIFTY, BANKNIFTY or RELIANCE.">
                {(id) => (
                  <Input
                    id={id}
                    className="h-12 text-base uppercase"
                    autoCapitalize="characters"
                    value={draft.underlying}
                    onChange={(e) => set('underlying', e.target.value)}
                  />
                )}
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Choice
                  label="Exchange"
                  value={draft.exchange}
                  options={[
                    ['NFO', 'NFO'],
                    ['BFO', 'BFO'],
                  ]}
                  onChange={(value) => set('exchange', value)}
                />
                <Choice
                  label="Contract"
                  value={ruleOf(draft)}
                  options={draft.kind === 'option' ? OPTION_RULES : FUTURE_RULES}
                  onChange={(value) => set('expiry_rule', value)}
                />
              </div>
              {draft.kind === 'option' && (
                <>
                  <Choice
                    label="Strike"
                    value={draft.strike_offset}
                    options={STRIKES}
                    onChange={(value) => set('strike_offset', value)}
                  />
                  <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
                    Options are only bought: a long alert buys a call, a short alert buys a put, and
                    a flat alert sells what was bought. The strike is counted from the underlying's
                    price when the alert arrives.
                  </p>
                </>
              )}
            </>
          )}
          <Field
            label={draft.kind === 'same' ? 'TradingView chart' : 'TradingView chart (optional)'}
            hint="As TradingView shows it, exchange first: NSE:NIFTY1! or NSE:RELIANCE. An alert from another chart is not sent."
          >
            {(id) => (
              <Input
                id={id}
                className="h-12 text-base uppercase"
                autoCapitalize="characters"
                value={draft.chart}
                onChange={(e) => set('chart', e.target.value)}
              />
            )}
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Choice
              label="Product"
              value={formOf(draft).product}
              options={
                lots
                  ? [
                      ['MIS', 'Intraday (MIS)'],
                      ['NRML', 'Carry (NRML)'],
                    ]
                  : [
                      ['MIS', 'Intraday (MIS)'],
                      ['CNC', 'Delivery (CNC)'],
                    ]
              }
              onChange={(value) => set('product', value)}
            />
            {!scaledOf(draft) && (
              <Field label={lots ? 'Lots' : 'Shares'}>
                {(id) => (
                  <Input
                    id={id}
                    className="h-12 text-base"
                    inputMode="numeric"
                    value={draft.size}
                    onChange={(e) => set('size', e.target.value)}
                  />
                )}
              </Field>
            )}
          </div>
          {draft.kind !== 'option' && (
            <Choice
              label="Size"
              value={draft.size_mode}
              options={[
                ['fixed', 'Fixed'],
                ['scaled', "Scaled from the chart's size"],
              ]}
              onChange={(value) => set('size_mode', value)}
            />
          )}
          {scaledOf(draft) && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Multiplier">
                  {(id) => (
                    <Input
                      id={id}
                      className="h-12 text-base"
                      inputMode="decimal"
                      value={draft.multiplier}
                      onChange={(e) => set('multiplier', e.target.value)}
                    />
                  )}
                </Field>
                <Field label={lots ? 'Cap, lots' : 'Cap, shares'}>
                  {(id) => (
                    <Input
                      id={id}
                      className="h-12 text-base"
                      inputMode="numeric"
                      value={draft.max_lots}
                      onChange={(e) => set('max_lots', e.target.value)}
                    />
                  )}
                </Field>
              </div>
              <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
                The quantity is the chart's position size times the multiplier. It must come to
                whole {lots ? 'lots' : 'shares'} and stay at or under the cap, or nothing is sent.
                Needs the strategy alert message, which carries the size.
              </p>
            </>
          )}
          <Choice
            label="Direction"
            value={draft.direction}
            options={[
              ['both', 'Long and short'],
              ['long', 'Long only'],
              ['short', 'Short only'],
            ]}
            onChange={(value) => set('direction', value)}
          />
        </fieldset>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Late limit, seconds" hint="An alert later than this is not sent.">
            {(id) => (
              <Input
                id={id}
                className="h-12 text-base"
                inputMode="numeric"
                value={draft.max_age_s}
                onChange={(e) => set('max_age_s', e.target.value)}
              />
            )}
          </Field>
          <Field label="Most alerts a day" hint="After these, no new entries today.">
            {(id) => (
              <Input
                id={id}
                className="h-12 text-base"
                inputMode="numeric"
                value={draft.max_alerts_day}
                onChange={(e) => set('max_alerts_day', e.target.value)}
              />
            )}
          </Field>
        </div>
        {refusal && (
          <p role="alert" className="sd-risk-note">
            <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
            {refusal}
          </p>
        )}
        <Button type="submit" className="h-12 text-base" disabled={sending}>
          {sending && <Loader2 aria-hidden="true" className="size-5 animate-spin" />}
          {strategy ? 'Save' : 'Create strategy'}
        </Button>
      </form>
    </BottomSheet>
  )
}
