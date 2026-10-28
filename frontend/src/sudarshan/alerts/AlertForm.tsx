import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Info, LoaderCircle, Search } from 'lucide-react'
import { type ReactNode, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import {
  type AlertDraft,
  type AlertExchange,
  type AlertKind,
  type AlertRow,
  type AlertsError,
  type BreakParams,
  type Candle,
  type Frequency,
  type MarketAlertsState,
  type MoveParams,
  marketAlertsApi,
  type Params,
  type Prefs,
  type PriceOp,
  type PriceParams,
  type PriceSource,
  problemOf,
  type VwapParams,
} from '@/api/marketAlerts'
import { SymbolSearchDialog } from '@/components/trading/SymbolSearchDialog'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import type { SearchRow } from '@/lib/trading/terminal'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { ACTION, OUTLINED, WarningLine } from '@/sudarshan/books/cards/parts'
import { searchSymbols } from '@/sudarshan/chart/api'
import { useNow } from '@/sudarshan/dashboard/hooks'
import { keyOf, priceOf } from '@/sudarshan/dashboard/live'
import { useFrameLines } from '@/sudarshan/live/useFrameLines'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe, isIndex } from '@/sudarshan/watchlist/rows'
import { toast } from '@/utils/toast'
import { PriceLine } from './AlertCard'
import { ALERTS_PATH } from './marketAlerts'
import { needsQuote, segmentOpen } from './measure'
import { dropRow, PREFS_KEY, putRow, STATE_KEY } from './stateCache'
import {
  checksAtWords,
  isCovered,
  isDerivative,
  KIND_DEFAULTS,
  levelFrom,
  NOT_FOR,
  OP_WORDS,
  priceNote,
  tickOf,
} from './words'

/** A new alert's instrument and price, from a watchlist symbol or the chart. */
export interface AlertPrefill {
  symbol: string
  exchange: string
  /** The tick the master contract gives, for the "Checks at" hint. */
  tick: string | number | null
  /** The live price when the form opened: the level starts from it, snapped to the paisa. */
  price: number | null
}

interface Picked {
  symbol: string
  exchange: AlertExchange
  tick: string | null
}

interface Form {
  instrument: Picked | null
  kind: AlertKind
  op: PriceOp
  level: string
  source: PriceSource
  move: MoveParams['direction']
  percent: string
  base: 'close' | 'open'
  side: BreakParams['side']
  after: string
  vwap: VwapParams['direction']
  frequency: Frequency
  candle: Candle
  cooldown: number
  /** `keep` (an edit) and `default` (a new one) leave `ends` out. */
  ends: 'default' | 'keep' | 'date' | 'none'
  endDate: string
  channels: Prefs | null
  note: string
}

function initialForm(row: AlertRow | null, prefill: AlertPrefill | null): Form {
  const blank: Form = {
    instrument: null,
    kind: 'price',
    op: 'crosses_above',
    level: '',
    source: 'ltp',
    move: 'up',
    percent: '',
    base: 'close',
    side: 'high',
    after: '09:30',
    vwap: 'above',
    frequency: 'once',
    candle: 'day',
    cooldown: 0,
    ends: 'default',
    endDate: '',
    channels: null,
    note: '',
  }
  if (!row) {
    if (!prefill || !isCovered(prefill.exchange)) return blank
    const exchange = prefill.exchange.toUpperCase() as AlertExchange
    return {
      ...blank,
      instrument: { symbol: prefill.symbol, exchange, tick: tickOf(exchange, prefill.tick) },
      level: levelFrom(prefill.price),
    }
  }
  const params = row.params
  const price = row.kind === 'price' ? (params as PriceParams) : null
  const move = row.kind === 'move' ? (params as MoveParams) : null
  const broken = row.kind === 'day_break' ? (params as BreakParams) : null
  return {
    ...blank,
    instrument: { symbol: row.symbol, exchange: row.exchange, tick: row.tick_size },
    kind: row.kind,
    op: price?.op ?? blank.op,
    level: price?.level ?? '',
    source: price?.source ?? 'ltp',
    move: move?.direction ?? 'up',
    percent: move?.percent ?? '',
    base: move?.base ?? 'close',
    side: broken?.side ?? 'high',
    after: broken?.after ?? '09:30',
    vwap: row.kind === 'vwap' ? (params as VwapParams).direction : 'above',
    frequency: row.frequency,
    candle: row.candle_min === null || row.candle_min === 0 ? 'day' : row.candle_min,
    cooldown: row.cooldown_s,
    ends: 'keep',
    channels: row.channels,
    note: row.note,
  }
}

function paramsOf(form: Form): Params {
  if (form.kind === 'price') return { op: form.op, level: form.level.trim(), source: form.source }
  if (form.kind === 'move') {
    return { base: form.base, direction: form.move, percent: form.percent.trim() }
  }
  if (form.kind === 'day_break') return { side: form.side, after: form.after }
  return { direction: form.vwap }
}

/** The whole draft, every field sent: an edit keeps what it does not change only by sending it. */
function draftOf(form: Form, instrument: Picked, price: number | null): AlertDraft {
  const draft: AlertDraft = {
    symbol: instrument.symbol,
    exchange: instrument.exchange,
    kind: form.kind,
    params: paramsOf(form),
    frequency: form.frequency,
    cooldown_s: form.cooldown,
    note: form.note.trim(),
    channels: form.channels,
  }
  if (form.frequency === 'per_candle' || form.frequency === 'at_close') draft.candle = form.candle
  if (form.ends === 'date') draft.ends = form.endDate
  if (form.ends === 'none') draft.ends = null
  if (price !== null) draft.price = price
  return draft
}

const KINDS: Array<{ value: AlertKind; label: string }> = [
  { value: 'price', label: 'Price' },
  { value: 'move', label: '% move' },
  { value: 'day_break', label: 'Day high/low' },
  { value: 'vwap', label: 'VWAP' },
]
const OPS = Object.entries(OP_WORDS).map(([value, label]) => ({ value: value as PriceOp, label }))
const FREQUENCIES: Array<{ value: Frequency; label: string }> = [
  { value: 'once', label: 'Once' },
  { value: 'per_candle', label: 'Once per candle' },
  { value: 'at_close', label: 'At candle close' },
  { value: 'every_time', label: 'Every time' },
]
const CANDLES: Array<{ value: Candle; label: string }> = [
  { value: 1, label: '1 min' },
  { value: 5, label: '5 min' },
  { value: 15, label: '15 min' },
  { value: 30, label: '30 min' },
  { value: 60, label: '60 min' },
  { value: 'day', label: 'Day' },
]
const WAITS = [0, 60, 300, 900, 1800, 3600]

function waitWords(seconds: number): string {
  if (seconds === 0) return 'No wait'
  if (seconds % 3600 === 0) return seconds === 3600 ? '1 hour' : `${seconds / 3600} hours`
  if (seconds % 60 === 0) return `${seconds / 60} min`
  return `${seconds} s`
}

/** The day today in India, YYYY-MM-DD, the earliest end date. */
function istToday(): string {
  return new Date(Date.now() + 5.5 * 3_600_000).toISOString().slice(0, 10)
}

const DAY_MONTH = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
})

/** An edit's end as it is now: the last day it works, or none of its own. */
function endWords(endsAt: number | null): string {
  // The end is the moment it stops, the midnight after its last day.
  return endsAt === null ? 'No end date' : `Works through ${DAY_MONTH.format(endsAt - 1)}`
}

const FIELD = 'flex flex-col gap-1.5'
const LEGEND = 'pb-1 text-xs font-semibold text-[color:var(--text-secondary)]'
const BOX = 'h-12 text-base'
const SELECT =
  'h-12 w-full rounded-md border border-input bg-card px-3 text-base outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

/** A row of choices: one pressed; one that cannot be had is off, and says why under the row. */
function Choices<T extends string | number>({
  legend,
  value,
  options,
  onPick,
  error,
}: {
  legend: string
  value: T
  options: Array<{ value: T; label: string; off?: string }>
  onPick: (value: T) => void
  error?: ReactNode
}) {
  const reasons = [...new Set(options.map((option) => option.off).filter(Boolean))]
  return (
    <fieldset className={FIELD}>
      <legend className={LEGEND}>{legend}</legend>
      <div className="flex flex-wrap gap-1 rounded-[0.875rem] bg-[color:var(--surface-2)] p-1">
        {options.map((option) => {
          const on = option.value === value
          return (
            <button
              key={String(option.value)}
              type="button"
              aria-pressed={on}
              disabled={option.off !== undefined}
              onClick={() => onPick(option.value)}
              className={cn(
                'flex min-h-12 grow items-center justify-center rounded-[0.6875rem] px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
                on
                  ? 'border border-primary bg-card font-bold text-primary shadow-[var(--shadow-card)]'
                  : 'font-semibold text-[color:var(--text-secondary)]'
              )}
            >
              {option.label}
            </button>
          )
        })}
      </div>
      {reasons.map((reason) => (
        <p
          key={reason}
          className="flex items-start gap-1.5 text-xs font-medium text-muted-foreground"
        >
          <Info aria-hidden="true" className="mt-px size-3.5 shrink-0" />
          {reason}
        </p>
      ))}
      {error}
    </fieldset>
  )
}

/** A field: its label, for its control alone, then what is said under it. */
function Field({
  id,
  label,
  children,
  after,
}: {
  id: string
  label: string
  children: ReactNode
  after?: ReactNode
}) {
  return (
    <div className={FIELD}>
      <label htmlFor={id} className={LEGEND}>
        {label}
      </label>
      {children}
      {after}
    </div>
  )
}

/** A field's own error from the answer: the server's sentence, with a mark beside it. */
function FieldError({ id, text }: { id: string; text: string | null }) {
  if (!text) return null
  return (
    <p
      id={id}
      role="alert"
      className="flex items-start gap-1.5 text-sm font-semibold text-[color:var(--down)]"
    >
      <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      {text}
    </p>
  )
}

/**
 * The form for a new alert, or for one already set (M10.3), in a sheet: the instrument, the kind
 * (Price, % move, Day high/low, VWAP) and its fields, how often, the wait between alerts, the end,
 * where it is sent and a note. An edit opens on the alert's own settings and sends them all back,
 * changed or not, but its end only when changed. The level is read back as the engine will read
 * it ("Checks at ₹100.05 (nearest tick)"), and the live price says when a crossing would wait or
 * a level check would fire at once. Save sends once; a refusal marks the field it names, in the
 * server's words.
 */
export function AlertFormSheet({
  row = null,
  prefill = null,
  onClose,
  onSaved,
}: {
  /** The alert to edit; none for a new one. */
  row?: AlertRow | null
  prefill?: AlertPrefill | null
  onClose: () => void
  onSaved: (row: AlertRow, created: boolean) => void
}) {
  const client = useQueryClient()
  const apiKey = useAuthStore((state) => state.apiKey)
  const state = client.getQueryData<MarketAlertsState>(STATE_KEY)
  const [form, setForm] = useState(() => initialForm(row, prefill))
  // The version the form opened on: an edit made elsewhere meanwhile meets the server's conflict.
  const [version] = useState(() => row?.version ?? 0)
  const [problem, setProblem] = useState<{ field: string | null; text: string } | null>(() =>
    prefill && !isCovered(prefill.exchange) ? { field: 'instrument', text: NOT_FOR.exchange } : null
  )
  const [searching, setSearching] = useState(false)
  const [saving, setSaving] = useState(false)
  const once = useRef(false)
  const typedLevel = useRef(form.level !== '')
  const ids = useId()
  const fieldId = (field: string) => `${ids}-${field}`
  const inputId = (field: string) => `${ids}-input-${field}`

  const { instrument } = form
  const index = instrument !== null && isIndex(instrument.exchange)
  const draftRow = useMemo(
    () =>
      ({
        kind: form.kind,
        params: paramsOf(form),
        exchange: instrument?.exchange ?? 'NSE',
        auction: null,
        check: null,
      }) as unknown as AlertRow,
    [form, instrument]
  )
  const instruments = useMemo(
    () => (instrument ? [{ symbol: instrument.symbol, exchange: instrument.exchange }] : []),
    [instrument]
  )
  const { lines, feed } = useFrameLines({
    instruments,
    mode: needsQuote(draftRow) ? 'Quote' : 'LTP',
    enabled: apiKey !== null,
  })
  const line = instrument ? lines.get(keyOf(instrument.symbol, instrument.exchange)) : undefined
  // Until its first tick, the price the form opened with, for that instrument only.
  const opened =
    prefill &&
    instrument &&
    prefill.symbol === instrument.symbol &&
    prefill.exchange.toUpperCase() === instrument.exchange
      ? prefill.price
      : null
  const ltp = priceOf(line)?.ltp ?? opened
  const watched =
    form.source === 'bid'
      ? (line?.data.bid_price ?? null)
      : form.source === 'ask'
        ? (line?.data.ask_price ?? null)
        : ltp

  // A new alert's level starts from the first live price, until the owner types one.
  useEffect(() => {
    if (row || typedLevel.current || form.level !== '' || ltp === null) return
    setForm((now) => (now.level === '' ? { ...now, level: levelFrom(ltp) } : now))
  }, [row, ltp, form.level])

  const set = (patch: Partial<Form>, field?: string) => {
    setForm((now) => ({ ...now, ...patch }))
    if (!field || problem?.field === field || problem?.field === null) setProblem(null)
  }
  // A kind brings its own defaults only when it changes: the one already pressed keeps the rest.
  const pickKind = (kind: AlertKind) => {
    if (kind !== form.kind) set(kindDefaults(kind), 'kind')
  }
  // A refusal marks its field where the field is on screen, and shows above Save otherwise.
  const shown = shownFields(form)
  const marked = problem?.field && shown.has(problem.field) ? problem.field : null
  const errorOf = (field: string) =>
    marked === field && problem ? <FieldError id={fieldId(field)} text={problem.text} /> : null
  const invalid = (field: string) =>
    marked === field ? { 'aria-invalid': true, 'aria-describedby': fieldId(field) } : {}
  // biome-ignore lint/correctness/useExhaustiveDependencies: each new refusal is the trigger
  useEffect(() => {
    if (marked) document.getElementById(`${ids}-${marked}`)?.scrollIntoView({ block: 'center' })
  }, [ids, marked, problem])

  const name = instrument ? describe(instrument.symbol, instrument.exchange).title : ''
  const checksAt =
    instrument && form.kind === 'price'
      ? checksAtWords(instrument.exchange, form.op, form.level, instrument.tick)
      : null
  const hint =
    instrument && form.kind === 'price'
      ? priceNote(name, instrument.exchange, form.op, form.level, watched)
      : null
  // Where alerts go by default, read from the server each time the form opens: the switches
  // take a tap only once that read has answered (/state's copy only fills in meanwhile).
  const settings = useQuery<Prefs, AlertsError>({
    queryKey: PREFS_KEY,
    queryFn: marketAlertsApi.prefs,
    placeholderData: state?.prefs,
    staleTime: 0,
    retry: false,
  })
  const settled =
    settings.data !== undefined &&
    !settings.isPlaceholderData &&
    !settings.isFetching &&
    !settings.isError
  const channels = form.channels ?? settings.data ?? null
  const atClose =
    form.kind === 'day_break'
      ? NOT_FOR.breakAtClose
      : form.kind === 'price' && form.source !== 'ltp'
        ? NOT_FOR.quoteAtClose
        : undefined
  const waits = WAITS.includes(form.cooldown)
    ? WAITS
    : [...WAITS, form.cooldown].sort((a, b) => a - b)

  const pick = (found: SearchRow) => {
    setSearching(false)
    const exchange = String(found.exchange ?? '').toUpperCase()
    if (!isCovered(exchange)) {
      setProblem({ field: 'instrument', text: NOT_FOR.exchange })
      return
    }
    typedLevel.current = false
    const quoted = isIndex(exchange) // no VWAP, no bid or ask
    set(
      {
        instrument: {
          symbol: String(found.symbol),
          exchange,
          tick: tickOf(exchange, found.tick_size),
        },
        level: '',
        ...(quoted && form.kind === 'vwap' ? kindDefaults('price') : {}),
        ...(quoted ? { source: 'ltp' as const } : {}),
      },
      'instrument'
    )
  }
  const search = useCallback(
    (query: string, exchange?: string, limit?: number) =>
      apiKey ? searchSymbols(apiKey, query, exchange, limit) : Promise.resolve([]),
    [apiKey]
  )

  const save = async () => {
    if (!instrument || once.current) return
    once.current = true
    setSaving(true)
    setProblem(null)
    const draft = draftOf(form, instrument, ltp)
    try {
      const saved = row
        ? await marketAlertsApi.change(row.id, { version, action: 'edit', alert: draft })
        : await marketAlertsApi.create(draft)
      client.setQueryData<MarketAlertsState>(STATE_KEY, (held) =>
        held ? putRow(held, saved) : held
      )
      onSaved(saved, !row)
    } catch (error) {
      const found = problemOf(error)
      once.current = false
      setSaving(false)
      if (found.code === 'gone' && row) {
        client.setQueryData<MarketAlertsState>(STATE_KEY, (held) =>
          held ? dropRow(held, row.id) : held
        )
        toast.info(found.message)
        return
      }
      const text =
        found.code === 'unanswered'
          ? 'No answer came, so this alert may or may not be saved. Your alerts are read again; look there before saving it again.'
          : found.message
      setProblem({ field: found.field, text })
      if (['conflict', 'refused', 'unanswered'].includes(found.code)) {
        void client.invalidateQueries({ queryKey: STATE_KEY })
      }
    }
  }

  const general = problem && !marked ? problem.text : null

  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={row ? 'Edit alert' : 'New alert'}
      description={instrument ? describe(instrument.symbol, instrument.exchange).detail : undefined}
    >
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
        className="flex flex-col gap-4"
      >
        <section className={FIELD} aria-label="Instrument">
          <button
            type="button"
            onClick={() => setSearching(true)}
            aria-describedby={marked === 'instrument' ? fieldId('instrument') : undefined}
            className={cn(OUTLINED, 'justify-between')}
          >
            <span className="truncate">{instrument ? name : 'Pick an instrument'}</span>
            <span className="inline-flex items-center gap-1 text-sm text-[color:var(--brand)]">
              <Search aria-hidden="true" className="size-4" />
              {instrument ? 'Change' : 'Search'}
            </span>
          </button>
          {instrument && apiKey !== null && (
            <FormPrice
              row={draftRow}
              line={line}
              feed={feed}
              marketOpen={segmentOpen(state?.segments, {
                instrument_key: `${SEGMENT[instrument.exchange]}|`,
              })}
            />
          )}
          {errorOf('instrument')}
        </section>

        <Choices
          legend="Alert when"
          value={form.kind}
          options={KINDS.map((kind) => ({
            ...kind,
            off: kind.value === 'vwap' && index ? NOT_FOR.indexVwap : undefined,
          }))}
          onPick={pickKind}
          error={errorOf('kind')}
        />

        {form.kind === 'price' && (
          <>
            <Field id={inputId('op')} label="Check" after={errorOf('op')}>
              <select
                id={inputId('op')}
                value={form.op}
                onChange={(event) => set({ op: event.target.value as PriceOp }, 'op')}
                className={SELECT}
                {...invalid('op')}
              >
                {OPS.map((op) => (
                  <option key={op.value} value={op.value}>
                    {op.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              id={inputId('level')}
              label="Price level"
              after={
                <>
                  {checksAt && (
                    <span className="text-sm font-medium text-[color:var(--text-secondary)]">
                      {checksAt}
                    </span>
                  )}
                  {errorOf('level')}
                </>
              }
            >
              <Input
                id={inputId('level')}
                inputMode="decimal"
                autoComplete="off"
                value={form.level}
                onChange={(event) => {
                  typedLevel.current = true
                  set({ level: event.target.value }, 'level')
                }}
                className={BOX}
                {...invalid('level')}
              />
            </Field>
            {hint?.warn && <WarningLine text={hint.text} />}
            {hint && !hint.warn && (
              <p className="flex items-start gap-1.5 text-sm font-medium text-[color:var(--info)]">
                <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                {hint.text}
              </p>
            )}
            <Choices
              legend="Watch"
              value={form.source}
              options={[
                { value: 'ltp' as const, label: 'Last price' },
                {
                  value: 'bid' as const,
                  label: 'Bid',
                  off: index ? NOT_FOR.indexQuotes : undefined,
                },
                {
                  value: 'ask' as const,
                  label: 'Ask',
                  off: index ? NOT_FOR.indexQuotes : undefined,
                },
              ]}
              onPick={(source) =>
                set(
                  {
                    source,
                    frequency:
                      source !== 'ltp' && form.frequency === 'at_close' ? 'once' : form.frequency,
                  },
                  'source'
                )
              }
              error={errorOf('source')}
            />
          </>
        )}

        {form.kind === 'move' && (
          <>
            <Choices
              legend="Way"
              value={form.move}
              options={[
                { value: 'up' as const, label: 'Up' },
                { value: 'down' as const, label: 'Down' },
                { value: 'either' as const, label: 'Either way' },
              ]}
              onPick={(move) => set({ move }, 'direction')}
              error={errorOf('direction')}
            />
            <Field id={inputId('percent')} label="Move, in %" after={errorOf('percent')}>
              <Input
                id={inputId('percent')}
                inputMode="decimal"
                autoComplete="off"
                value={form.percent}
                onChange={(event) => set({ percent: event.target.value }, 'percent')}
                className={BOX}
                {...invalid('percent')}
              />
            </Field>
            <Choices
              legend="From"
              value={form.base}
              options={[
                { value: 'close' as const, label: 'Previous close' },
                { value: 'open' as const, label: 'Today’s open' },
              ]}
              onPick={(base) => set({ base }, 'base')}
              error={errorOf('base')}
            />
          </>
        )}

        {form.kind === 'day_break' && (
          <>
            <Choices
              legend="Breaks"
              value={form.side}
              options={[
                { value: 'high' as const, label: 'Day high' },
                { value: 'low' as const, label: 'Day low' },
              ]}
              onPick={(side) => set({ side }, 'side')}
              error={errorOf('side')}
            />
            <Field id={inputId('after')} label="From" after={errorOf('after')}>
              <Input
                id={inputId('after')}
                type="time"
                value={form.after}
                onChange={(event) => set({ after: event.target.value }, 'after')}
                className={BOX}
                {...invalid('after')}
              />
            </Field>
          </>
        )}

        {form.kind === 'vwap' && (
          <Choices
            legend="Crosses VWAP"
            value={form.vwap}
            options={[
              { value: 'above' as const, label: 'Above' },
              { value: 'below' as const, label: 'Below' },
              { value: 'either' as const, label: 'Either way' },
            ]}
            onPick={(vwap) => set({ vwap }, 'direction')}
            error={errorOf('direction')}
          />
        )}

        <Choices
          legend="How often"
          value={form.frequency}
          options={FREQUENCIES.map((each) => ({
            ...each,
            off: each.value === 'at_close' ? atClose : undefined,
          }))}
          onPick={(frequency) => set({ frequency }, 'frequency')}
          error={errorOf('frequency')}
        />
        {(form.frequency === 'per_candle' || form.frequency === 'at_close') && (
          <Choices
            legend="Candle"
            value={form.candle}
            options={CANDLES}
            onPick={(candle) => set({ candle }, 'candle')}
            error={errorOf('candle')}
          />
        )}

        <Field id={inputId('cooldown_s')} label="Wait between alerts" after={errorOf('cooldown_s')}>
          <select
            id={inputId('cooldown_s')}
            value={form.cooldown}
            onChange={(event) => set({ cooldown: Number(event.target.value) }, 'cooldown_s')}
            className={SELECT}
            {...invalid('cooldown_s')}
          >
            {waits.map((seconds) => (
              <option key={seconds} value={seconds}>
                {waitWords(seconds)}
              </option>
            ))}
          </select>
        </Field>

        <Choices
          legend="Ends"
          value={form.ends}
          options={[
            row
              ? { value: 'keep' as const, label: 'As now' }
              : {
                  value: 'default' as const,
                  label:
                    instrument && isDerivative(instrument.exchange) ? 'At expiry' : 'In 60 days',
                },
            { value: 'date' as const, label: 'On a date' },
            { value: 'none' as const, label: 'No end' },
          ]}
          onPick={(ends) => set({ ends }, 'ends')}
          error={form.ends === 'date' ? null : errorOf('ends')}
        />
        {row && form.ends === 'keep' && (
          <p className="-mt-2 text-sm font-medium text-[color:var(--text-secondary)]">
            {endWords(row.ends_at)}
          </p>
        )}
        {form.ends === 'date' && (
          <Field id={inputId('ends')} label="Works through" after={errorOf('ends')}>
            <Input
              id={inputId('ends')}
              type="date"
              min={istToday()}
              value={form.endDate}
              onChange={(event) => set({ endDate: event.target.value }, 'ends')}
              className={BOX}
              {...invalid('ends')}
            />
          </Field>
        )}

        <fieldset className={FIELD}>
          <legend className={LEGEND}>Send to</legend>
          {channels &&
            (['telegram', 'app'] as const).map((channel) => (
              <label
                key={channel}
                className="flex min-h-12 items-center justify-between gap-3 rounded-[0.875rem] border px-3"
              >
                <span className="text-sm font-semibold">
                  {channel === 'telegram' ? 'Telegram' : 'App'}
                </span>
                <Switch
                  checked={channels[channel]}
                  disabled={form.channels === null && !settled}
                  onCheckedChange={(on) =>
                    set({ channels: { ...channels, [channel]: on } }, 'channels')
                  }
                />
              </label>
            ))}
          {form.channels === null ? (
            <SettingsWords settled={settled} error={settings.error} />
          ) : (
            <button
              type="button"
              onClick={() => set({ channels: null }, 'channels')}
              className="min-h-12 self-start text-sm font-semibold text-[color:var(--brand)] underline underline-offset-2"
            >
              Follow my alert settings instead
            </button>
          )}
          {errorOf('channels')}
        </fieldset>

        <Field id={inputId('note')} label="Note (optional)" after={errorOf('note')}>
          <Input
            id={inputId('note')}
            maxLength={120}
            value={form.note}
            onChange={(event) => set({ note: event.target.value }, 'note')}
            className={BOX}
            {...invalid('note')}
          />
        </Field>

        {general && (
          <p
            role="alert"
            className="flex items-start gap-1.5 rounded-[0.625rem] bg-[color:var(--down-tint)] px-3 py-2 text-sm font-semibold text-[color:var(--down)]"
          >
            <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {general}
          </p>
        )}

        <div className="grid grid-cols-2 gap-2">
          <button type="button" onClick={onClose} className={OUTLINED}>
            Cancel
          </button>
          <button
            type="submit"
            disabled={!instrument || saving}
            className={cn(ACTION, 'bg-primary text-primary-foreground hover:bg-primary/90')}
          >
            {saving ? 'Saving' : 'Save'}
          </button>
        </div>
      </form>

      <SymbolSearchDialog
        open={searching}
        onOpenChange={(open) => setSearching(open)}
        search={search}
        initialQuery={instrument?.symbol ?? ''}
        onPick={pick}
        title="Instrument"
      />
    </BottomSheet>
  )
}

/**
 * A save made away from the Alerts page (a watchlist symbol, the chart): "Alert set: NIFTY", the
 * condition in the server's words, and View to the alert's sheet.
 */
export function toastAlertSet(row: AlertRow, go: (to: string) => void): void {
  toast.success(`Alert set: ${describe(row.symbol, row.exchange).title}`, {
    description: row.words.condition,
    duration: 6_000,
    classNames: { actionButton: 'min-h-12 min-w-12' },
    action: { label: 'View', onClick: () => go(`${ALERTS_PATH}?alert=${row.id}`) },
  })
}

/** A kind as it starts: its own frequency, candle and wait (contract 1.3). */
function kindDefaults(kind: AlertKind): Partial<Form> {
  const start = KIND_DEFAULTS[kind]
  return { kind, frequency: start.frequency, candle: start.candle, cooldown: start.cooldown }
}

/** The fields on screen for the form as it stands, which a refusal can mark. */
function shownFields(form: Form): Set<string> {
  const kind = {
    price: ['op', 'level', 'source'],
    move: ['direction', 'percent', 'base'],
    day_break: ['side', 'after'],
    vwap: ['direction'],
  }[form.kind]
  const candle = form.frequency === 'per_candle' || form.frequency === 'at_close' ? ['candle'] : []
  const common = ['instrument', 'kind', 'frequency', 'cooldown_s', 'ends', 'channels', 'note']
  return new Set([...common, ...kind, ...candle])
}

/** What "Send to" follows when the alert has no channels of its own, and whether it is known yet. */
function SettingsWords({ settled, error }: { settled: boolean; error: AlertsError | null }) {
  if (settled) {
    return <p className="text-xs font-medium text-muted-foreground">As in your alert settings.</p>
  }
  if (error) {
    return (
      <p className="flex items-start gap-1.5 text-xs font-medium text-[color:var(--warning-text)]">
        <AlertTriangle aria-hidden="true" className="mt-px size-3.5 shrink-0" />
        {`${error.message} This alert follows your alert settings.`}
      </p>
    )
  }
  return (
    <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
      <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" />
      Reading your alert settings
    </p>
  )
}

/** The form's price line, its age moved on every second by itself, not by the whole form. */
function FormPrice(props: Omit<Parameters<typeof PriceLine>[0], 'now'>) {
  const now = useNow()
  return <PriceLine {...props} now={now} />
}

/** OpenAlgo's exchange for each segment's key prefix, as spec.py has them. */
const SEGMENT: Record<AlertExchange, string> = {
  NSE: 'NSE_EQ',
  BSE: 'BSE_EQ',
  NFO: 'NSE_FO',
  BFO: 'BSE_FO',
  NSE_INDEX: 'NSE_INDEX',
  BSE_INDEX: 'BSE_INDEX',
}
