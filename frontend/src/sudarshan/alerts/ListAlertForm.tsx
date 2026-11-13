import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Info } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import {
  type AlertKind,
  type GroupAnswer,
  type GroupDelivery,
  type GroupDraft,
  type GroupRow,
  type MarketAlertsState,
  marketAlertsApi,
  problemOf,
} from '@/api/marketAlerts'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { ACTION, OUTLINED } from '@/sudarshan/books/cards/parts'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import {
  BOX,
  CANDLES,
  Choices,
  endWords,
  Field,
  FieldError,
  type Form,
  FREQUENCIES,
  initialForm,
  istToday,
  kindDefaults,
  NO_ANSWER_SAVE,
  paramsOf,
  SELECT,
  WAITS,
  waitWords,
} from './AlertForm'
import { NO_ANSWER_HOLD_MS } from './actions'
import { BusyIcon, HELD } from './lines'
import { dropGroup, putGroup, STATE_KEY } from './stateCache'
import { NOT_FOR, NOT_PLAIN, plainNumber } from './words'

/**
 * The form for an alert over a list or a section (M8.14), in a sheet: "Every symbol of Swing
 * (24)". A price level is per symbol, so only a % move, a Day high or low break and a VWAP cross
 * are offered. Delivery on Telegram is one of two: the first alert at once and those that follow
 * within 5 minutes as one message (the default), or each alert at once. The name and the count in
 * the heading come from whoever opened the form and are only the heading: after Save the form says
 * what the service answered, as it came (whether it fits, both numbers, what is left out). A
 * refusal (it does not fit, a ranked list, a stale version) is shown in the service's words.
 * Editing a list alert opens on its own settings; its kind stays.
 */
export interface ListTarget {
  ref: string
  name: string
  count: number | null
  /** A screen feeds the list. */
  fed: boolean
}

const KIND_OPTIONS: Array<{ value: AlertKind; label: string }> = [
  { value: 'move', label: '% move' },
  { value: 'day_break', label: 'Day high/low' },
  { value: 'vwap', label: 'VWAP' },
]

const DELIVERIES: Array<{ value: GroupDelivery; label: string }> = [
  { value: 'first_then_digest', label: 'Alerts that follow within 5 minutes go as one message' },
  { value: 'each', label: 'Each alert at once' },
]

export const FED_HINT = 'A screen feeds this list. Its own alerts may suit better.'
const KEPT_KIND = 'A list alert keeps its kind. Set another alert to change it.'

/** "Every symbol of Swing (24)". */
export function everySymbol(name: string, count: number | null): string {
  const of = name.trim() || 'this list'
  return `Every symbol of ${of}${count === null ? '' : ` (${new Intl.NumberFormat('en-IN').format(count)})`}`
}

function startForm(group: GroupRow | null): Form {
  if (!group) return { ...initialForm(null, null), ...kindDefaults('move'), percent: '2' }
  const stand = { ...group, symbol: '', exchange: 'NSE', tick_size: '', channels: null }
  return initialForm(stand as never, null)
}

export function ListAlertFormSheet({
  target,
  group = null,
  onClose,
}: {
  /** A new list alert over this list. */
  target?: ListTarget
  /** The list alert to edit. */
  group?: GroupRow | null
  onClose: () => void
}) {
  const client = useQueryClient()
  const [form, setForm] = useState(() => startForm(group))
  const [delivery, setDelivery] = useState<GroupDelivery>(group?.delivery ?? 'first_then_digest')
  const [version] = useState(() => group?.version ?? 0)
  const [problem, setProblem] = useState<{ field: string | null; text: string } | null>(null)
  const [saving, setSaving] = useState<'sending' | 'held' | null>(null)
  const [done, setDone] = useState<GroupAnswer | null>(null)
  const once = useRef(false)
  const isOpen = useRef(true)
  const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    isOpen.current = true
    return () => {
      isOpen.current = false
      if (holdTimer.current) clearTimeout(holdTimer.current)
    }
  }, [])
  const ids = useId()
  const inputId = (field: string) => `${ids}-input-${field}`

  const set = (patch: Partial<Form>) => {
    setForm((now) => ({ ...now, ...patch }))
    setProblem(null)
  }
  const mark = (field: string) => (problem?.field === field ? problem : null)
  const invalid = (field: string) =>
    mark(field) ? { 'aria-invalid': true, 'aria-describedby': `${ids}-${field}` } : {}
  const errorOf = (field: string) => {
    const found = mark(field)
    return found ? <FieldError id={`${ids}-${field}`} text={found.text} /> : null
  }
  const named = group ? group.name : (target?.name ?? '')
  const heading = group
    ? everySymbol(named, group.members)
    : everySymbol(named, target?.count ?? null)
  const atClose = form.kind === 'day_break' ? NOT_FOR.breakAtClose : undefined
  const waits = WAITS.includes(form.cooldown)
    ? WAITS
    : [...WAITS, form.cooldown].sort((a, b) => a - b)

  const draft = (): GroupDraft => {
    const out: GroupDraft = {
      name: named,
      kind: form.kind,
      params: paramsOf(form),
      frequency: form.frequency,
      cooldown_s: form.cooldown,
      note: form.note.trim(),
      delivery,
      digest_min: group?.digest_min ?? 5,
    }
    if (!group && target) out.ref = target.ref
    if (form.frequency === 'per_candle' || form.frequency === 'at_close') out.candle = form.candle
    if (form.ends === 'date') out.ends = form.endDate
    if (form.ends === 'none') out.ends = null
    return out
  }

  const save = async () => {
    if (once.current) return
    if (form.kind === 'move' && !plainNumber(form.percent)) {
      setProblem({ field: 'percent', text: NOT_PLAIN.percent })
      return
    }
    once.current = true
    setSaving('sending')
    setProblem(null)
    try {
      const answer = group
        ? await marketAlertsApi.changeGroup(group.id, {
            version,
            action: 'edit',
            group: draft(),
          })
        : await marketAlertsApi.createGroup(draft())
      client.setQueryData<MarketAlertsState>(STATE_KEY, (held) =>
        held ? putGroup(held, answer.group) : held
      )
      // The member alerts are not in the answer: read them.
      void client.invalidateQueries({ queryKey: STATE_KEY })
      if (isOpen.current) {
        setDone(answer)
        setSaving(null)
      }
    } catch (error) {
      const found = problemOf(error)
      if (found.code === 'unanswered') {
        if (isOpen.current) {
          setSaving('held')
          setProblem({ field: null, text: NO_ANSWER_SAVE })
        }
        holdTimer.current = setTimeout(() => {
          holdTimer.current = null
          once.current = false
          if (isOpen.current) setSaving(null)
        }, NO_ANSWER_HOLD_MS)
        void client.invalidateQueries({ queryKey: STATE_KEY })
        return
      }
      once.current = false
      if (isOpen.current) setSaving(null)
      if (found.code === 'gone' && group) {
        client.setQueryData<MarketAlertsState>(STATE_KEY, (held) =>
          held ? dropGroup(held, group.id) : held
        )
      }
      if (found.code === 'conflict' || found.code === 'refused' || found.code === 'limit') {
        void client.invalidateQueries({ queryKey: STATE_KEY })
      }
      if (isOpen.current) setProblem({ field: found.field, text: found.message })
    }
  }

  const general = problem && problem.field !== 'percent' ? problem.text : null

  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={group ? 'Edit list alert' : 'Alert on a list'}
      description={heading}
    >
      {done ? (
        <Saved answer={done} edited={group !== null} onClose={onClose} />
      ) : (
        <form
          noValidate
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
          className="flex flex-col gap-4"
        >
          {target?.fed && (
            <p className="flex items-start gap-1.5 text-sm font-medium text-[color:var(--info)]">
              <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {FED_HINT}
            </p>
          )}

          <Choices
            legend="Alert when"
            value={form.kind}
            options={KIND_OPTIONS.map((kind) => ({
              ...kind,
              off: group && kind.value !== group.kind ? KEPT_KIND : undefined,
            }))}
            onPick={(kind) => kind !== form.kind && set(kindDefaults(kind))}
          />

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
                onPick={(move) => set({ move })}
              />
              <Field id={inputId('percent')} label="Move, in %" after={errorOf('percent')}>
                <Input
                  id={inputId('percent')}
                  inputMode="decimal"
                  autoComplete="off"
                  value={form.percent}
                  onChange={(event) => {
                    set({ percent: event.target.value })
                    if (!plainNumber(event.target.value)) {
                      setProblem({ field: 'percent', text: NOT_PLAIN.percent })
                    }
                  }}
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
                onPick={(base) => set({ base })}
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
                onPick={(side) => set({ side })}
              />
              <Field id={inputId('after')} label="From">
                <Input
                  id={inputId('after')}
                  type="time"
                  value={form.after}
                  onChange={(event) => set({ after: event.target.value })}
                  className={BOX}
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
              onPick={(vwap) => set({ vwap })}
            />
          )}

          <Choices
            legend="How often"
            value={form.frequency}
            options={FREQUENCIES.map((each) => ({
              ...each,
              off: each.value === 'at_close' ? atClose : undefined,
            }))}
            onPick={(frequency) => set({ frequency })}
          />
          {(form.frequency === 'per_candle' || form.frequency === 'at_close') && (
            <Choices
              legend="Candle"
              value={form.candle}
              options={CANDLES}
              onPick={(candle) => set({ candle })}
            />
          )}

          <Field id={inputId('cooldown_s')} label="Wait between alerts">
            <select
              id={inputId('cooldown_s')}
              value={form.cooldown}
              onChange={(event) => set({ cooldown: Number(event.target.value) })}
              className={SELECT}
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
              group
                ? { value: 'keep' as const, label: 'As now' }
                : { value: 'default' as const, label: 'At expiry or in 60 days' },
              { value: 'date' as const, label: 'On a date' },
              { value: 'none' as const, label: 'No end' },
            ]}
            onPick={(ends) => set({ ends })}
          />
          {group && form.ends === 'keep' && (
            <p className="-mt-2 text-sm font-medium text-[color:var(--text-secondary)]">
              {endWords(group.ends_at)}
            </p>
          )}
          {form.ends === 'date' && (
            <Field id={inputId('ends')} label="Works through" after={errorOf('ends')}>
              <Input
                id={inputId('ends')}
                type="date"
                min={istToday()}
                value={form.endDate}
                onChange={(event) => set({ endDate: event.target.value })}
                className={BOX}
                {...invalid('ends')}
              />
            </Field>
          )}

          <Choices
            legend="Telegram"
            value={delivery}
            options={DELIVERIES}
            onPick={(next) => {
              setDelivery(next)
              setProblem(null)
            }}
          />

          <Field id={inputId('note')} label="Note (optional)">
            <Input
              id={inputId('note')}
              maxLength={120}
              value={form.note}
              onChange={(event) => set({ note: event.target.value })}
              className={BOX}
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
              disabled={saving !== null}
              className={cn(ACTION, 'bg-primary text-primary-foreground hover:bg-primary/90', HELD)}
            >
              {saving === 'sending' && <BusyIcon />}
              {saving === 'sending' ? 'Saving' : 'Save'}
            </button>
          </div>
        </form>
      )}
    </BottomSheet>
  )
}

/** What the service answered to a make or an edit, as it came: whether it fits, what is left out. */
function Saved({
  answer,
  edited,
  onClose,
}: {
  answer: GroupAnswer
  edited: boolean
  onClose: () => void
}) {
  const { group, fit } = answer
  const lines = [fit, group.status_reason, ...group.left_out].filter(Boolean)
  return (
    <div className="flex flex-col gap-3" data-saved>
      <p className="text-base font-bold">{edited ? 'List alert saved' : 'List alert set'}</p>
      {lines.map((line) => (
        <p key={line} className="text-sm font-medium text-[color:var(--text-secondary)]">
          {line}
        </p>
      ))}
      <button
        type="button"
        onClick={onClose}
        className={cn(ACTION, 'bg-primary text-primary-foreground hover:bg-primary/90')}
      >
        Done
      </button>
    </div>
  )
}
