import { Bell, BellOff, Check, ListPlus, TriangleAlert, X } from 'lucide-react'
import { useId, useState } from 'react'
import { cn } from '@/lib/utils'
import { ACTION, OUTLINED } from '@/sudarshan/books/cards/parts'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import {
  DEFAULT_ALERTS,
  DEFAULT_GRACE,
  freshTarget,
  LIMITS,
  LIST_NAME_MAX,
  wholeProblem,
} from './settings'
import type { AlertSettings, Grace, ScreenSpec } from './types'

const INPUT =
  'min-h-12 w-full rounded-xl border border-input bg-card px-3 text-[0.9375rem] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring'
const CHOSEN = 'border-primary bg-[color:var(--brand-tint)] font-bold text-primary'

type Target = NonNullable<ScreenSpec['target']>

/** An on/off switch that says its state in a word and an icon, never in colour alone. */
function Switch({
  label,
  on,
  onChange,
  hint,
}: {
  label: string
  on: boolean
  onChange: (on: boolean) => void
  hint?: string
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-sm font-semibold">{label}</span>
        {hint && (
          <span className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
            {hint}
          </span>
        )}
      </span>
      <button
        type="button"
        aria-pressed={on}
        aria-label={label}
        onClick={() => onChange(!on)}
        className={cn(OUTLINED, 'w-24 shrink-0', on && CHOSEN)}
      >
        {on ? (
          <Check aria-hidden="true" className="size-4" />
        ) : (
          <X aria-hidden="true" className="size-4" />
        )}
        {on ? 'On' : 'Off'}
      </button>
    </div>
  )
}

/**
 * A whole number the owner types. It is applied only while it is valid; otherwise the service's own
 * sentence for it shows under the box and the last good value stays in the screen.
 */
function WholeField({
  label,
  value,
  limit,
  onChange,
}: {
  label: string
  value: number
  limit: { low: number; high: number; words: string }
  onChange: (value: number) => void
}) {
  const id = useId()
  const [text, setText] = useState(String(value))
  const problem = wholeProblem(text, limit)
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
      </label>
      <input
        id={id}
        type="text"
        inputMode="numeric"
        value={text}
        aria-invalid={problem !== null}
        onChange={(event) => {
          setText(event.target.value)
          if (wholeProblem(event.target.value, limit) === null) onChange(Number(event.target.value))
        }}
        className={cn(INPUT, problem && 'border-destructive')}
      />
      {problem && (
        <p
          role="alert"
          className="flex items-start gap-1.5 text-[0.8125rem] font-semibold text-destructive"
        >
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {problem}
        </p>
      )}
    </div>
  )
}

/** Add matches to a watchlist: which list, how long a miss is forgiven, how many a check. */
export function TargetSheet({
  open,
  name,
  target,
  lists,
  onChange,
  onClose,
}: {
  open: boolean
  name: string
  target: ScreenSpec['target']
  lists: Array<{ id: number; name: string }>
  onChange: (target: ScreenSpec['target']) => void
  onClose: () => void
}) {
  const set = (change: Partial<Target>) => target && onChange({ ...target, ...change })
  const grace = target?.grace ?? DEFAULT_GRACE
  const setGrace = (next: Grace) => set({ grace: next })
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Add matches to a watchlist"
    >
      <div className="flex flex-col gap-4">
        <Switch
          label="Add matches to a watchlist"
          on={target !== null && target !== undefined}
          onChange={(on) => onChange(on ? freshTarget(name) : null)}
          hint="Each match joins the list you pick and leaves it when it stops matching."
        />
        {target && (
          <>
            <fieldset className="flex flex-col gap-1.5">
              <legend className="pb-1 text-sm font-semibold">Which list</legend>
              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  aria-pressed={target.list_id === null}
                  onClick={() =>
                    set({
                      list_id: null,
                      create_name: target.create_name ?? freshTarget(name).create_name,
                    })
                  }
                  className={cn(OUTLINED, target.list_id === null && CHOSEN)}
                >
                  A new list
                </button>
                <button
                  type="button"
                  aria-pressed={target.list_id !== null}
                  disabled={lists.length === 0}
                  onClick={() =>
                    lists[0] && set({ list_id: target.list_id ?? lists[0].id, create_name: null })
                  }
                  className={cn(OUTLINED, target.list_id !== null && CHOSEN)}
                >
                  One of mine
                </button>
              </div>
              {target.list_id === null ? (
                <label className="flex flex-col gap-1 text-sm font-semibold">
                  Name of the new list
                  <input
                    type="text"
                    value={target.create_name ?? ''}
                    maxLength={LIST_NAME_MAX}
                    onChange={(event) => set({ create_name: event.target.value })}
                    className={INPUT}
                  />
                </label>
              ) : (
                <label className="flex flex-col gap-1 text-sm font-semibold">
                  Your list
                  <select
                    value={target.list_id}
                    onChange={(event) => set({ list_id: Number(event.target.value) })}
                    className={INPUT}
                  >
                    {lists.map((list) => (
                      <option key={list.id} value={list.id}>
                        {list.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </fieldset>

            <fieldset className="flex flex-col gap-1.5">
              <legend className="pb-1 text-sm font-semibold">When it stops matching</legend>
              <div className="flex flex-wrap gap-1.5">
                {(
                  [
                    ['runs', 'After some checks'],
                    ['day_end', 'At the end of the day'],
                    ['never', 'Never leave'],
                  ] as const
                ).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    aria-pressed={grace.mode === mode}
                    onClick={() =>
                      setGrace(mode === 'runs' ? { mode, n: grace.n ?? DEFAULT_GRACE.n } : { mode })
                    }
                    className={cn(OUTLINED, grace.mode === mode && CHOSEN)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {grace.mode === 'runs' && (
                <WholeField
                  key="grace"
                  label="Missed checks before it leaves"
                  value={grace.n ?? DEFAULT_GRACE.n ?? 2}
                  limit={LIMITS.graceRuns}
                  onChange={(n) => setGrace({ mode: 'runs', n })}
                />
              )}
            </fieldset>

            <WholeField
              label="Most to add in one check"
              value={target.max_adds_per_run}
              limit={LIMITS.maxAdds}
              onChange={(max_adds_per_run) => set({ max_adds_per_run })}
            />
          </>
        )}
        <button
          type="button"
          onClick={onClose}
          className={cn(ACTION, 'bg-primary text-primary-foreground hover:bg-primary/90')}
        >
          <ListPlus aria-hidden="true" className="size-5" />
          Done
        </button>
      </div>
    </BottomSheet>
  )
}

const COLUMNS = [
  ['ltp', 'Price'],
  ['change_pct', '% change'],
  ['value', 'Rank value'],
] as const

/** Alerts: when the screen tells you, how often, where, and which figures it shows. */
export function AlertsSheet({
  open,
  alerts,
  onChange,
  onClose,
}: {
  open: boolean
  alerts: AlertSettings | undefined
  onChange: (alerts: AlertSettings) => void
  onClose: () => void
}) {
  const have = alerts ?? DEFAULT_ALERTS
  const set = (change: Partial<AlertSettings>) => onChange({ ...have, ...change })
  const channels = have.channels ?? { telegram: true, app: true }
  const nowhere = have.on && have.channels !== null && !have.channels.telegram && !have.channels.app
  return (
    <BottomSheet open={open} onOpenChange={(next) => !next && onClose()} title="Alerts">
      <div className="flex flex-col gap-4">
        <Switch
          label="Alert me on matches"
          on={have.on}
          onChange={(on) => set({ on })}
          hint="Each check that finds something tells you, by the rules below."
        />
        {have.on && (
          <>
            <WholeField
              label="Fewest matches to tell"
              value={have.min_matches}
              limit={LIMITS.minMatches}
              onChange={(min_matches) => set({ min_matches })}
            />
            <Switch
              label="Tell each stock once a day"
              on={have.unique_per_day}
              onChange={(unique_per_day) => set({ unique_per_day })}
            />
            <Switch
              label="Skip checks with no matches"
              on={have.skip_zero}
              onChange={(skip_zero) => set({ skip_zero })}
            />
            <Switch
              label="Tell me when a stock leaves"
              on={have.tell_exits}
              onChange={(tell_exits) => set({ tell_exits })}
            />
            <fieldset className="flex flex-col gap-1.5">
              <legend className="pb-1 text-sm font-semibold">After the first alert</legend>
              <div className="flex flex-wrap gap-1.5">
                {(
                  [
                    ['continue', 'Keep alerting'],
                    ['pause', 'Pause alerts'],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={have.after_trigger === id}
                    onClick={() => set({ after_trigger: id })}
                    className={cn(OUTLINED, have.after_trigger === id && CHOSEN)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </fieldset>
            <WholeField
              label="Checks to wait between alerts"
              value={have.cooldown_runs}
              limit={LIMITS.cooldown}
              onChange={(cooldown_runs) => set({ cooldown_runs })}
            />
            <WholeField
              label="Most alerts for one stock a day"
              value={have.max_per_day_per_symbol}
              limit={LIMITS.perSymbol}
              onChange={(max_per_day_per_symbol) => set({ max_per_day_per_symbol })}
            />
            <fieldset className="flex flex-col gap-1.5">
              <legend className="pb-1 text-sm font-semibold">Send to</legend>
              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  aria-pressed={have.channels === null}
                  onClick={() => set({ channels: null })}
                  className={cn(OUTLINED, have.channels === null && CHOSEN)}
                >
                  Usual channels
                </button>
                {(
                  [
                    ['telegram', 'Telegram'],
                    ['app', 'The app'],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={have.channels !== null && channels[id]}
                    onClick={() =>
                      set({
                        channels: {
                          ...channels,
                          [id]: have.channels === null ? true : !channels[id],
                        },
                      })
                    }
                    className={cn(OUTLINED, have.channels !== null && channels[id] && CHOSEN)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {nowhere && (
                <p
                  role="alert"
                  className="flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]"
                >
                  <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                  Alerts are on but go nowhere. Pick Telegram, the app, or both.
                </p>
              )}
            </fieldset>
            <fieldset className="flex flex-col gap-1.5">
              <legend className="pb-1 text-sm font-semibold">Figures in the alert</legend>
              <div className="flex flex-wrap gap-1.5">
                {COLUMNS.map(([id, label]) => {
                  const on = have.columns.includes(id)
                  return (
                    <button
                      key={id}
                      type="button"
                      aria-pressed={on}
                      onClick={() => {
                        const next = on
                          ? have.columns.filter((column) => column !== id)
                          : COLUMNS.map(([column]) => column).filter(
                              (column) => column === id || have.columns.includes(column)
                            )
                        if (next.length > 0) set({ columns: next })
                      }}
                      className={cn(OUTLINED, on && CHOSEN)}
                    >
                      {on && <Check aria-hidden="true" className="size-4" />}
                      {label}
                    </button>
                  )
                })}
              </div>
              <p className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
                At least one stays on.
              </p>
            </fieldset>
          </>
        )}
        <button
          type="button"
          onClick={onClose}
          className={cn(ACTION, 'bg-primary text-primary-foreground hover:bg-primary/90')}
        >
          {have.on ? (
            <Bell aria-hidden="true" className="size-5" />
          ) : (
            <BellOff aria-hidden="true" className="size-5" />
          )}
          Done
        </button>
      </div>
    </BottomSheet>
  )
}
