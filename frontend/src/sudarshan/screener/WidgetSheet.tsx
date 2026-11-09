import { useQueryClient } from '@tanstack/react-query'
import { Check, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { OUTLINED } from '@/sudarshan/books/cards/parts'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { screenerApi } from './api'
import { guarded, noteWritten } from './dashboardWrites'
import { DASHBOARD_KEY, STATE_KEY } from './keys'
import { putRow } from './stateCache'
import { clausesOf } from './tree'
import type {
  ScreenerState,
  ScreenRow,
  WidgetCard,
  WidgetKind,
  WidgetSort,
  WidgetUpdate,
} from './types'
import { useSend } from './useSend'
import { clauseSentence } from './words'

const CHOSEN = 'border-primary bg-[color:var(--brand-tint)] font-bold text-primary'

/** The two ways a day-only screen is checked, as the screen's run (design 3.6). */
export const WHEN = {
  during_day: { on: 'close', tf: 15 },
  after_close: { on: 'after_close' },
} as const
export type When = keyof typeof WHEN

export const WHEN_WORDS: Record<When, string> = {
  during_day: 'During the day: the forming day candle at each 15-minute close',
  after_close: 'After the close (16:05)',
}

/** One choice of a group: a 48 dp button that says it is chosen with a tick and its state. */
export function Choice({
  label,
  chosen,
  onClick,
  disabled,
  wide,
}: {
  label: string
  chosen: boolean
  onClick: () => void
  disabled?: boolean
  wide?: boolean
}) {
  return (
    <button
      type="button"
      aria-pressed={chosen}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        OUTLINED,
        'justify-start text-left',
        wide ? 'w-full' : 'flex-1',
        chosen && CHOSEN
      )}
    >
      {chosen && <Check aria-hidden="true" className="size-4 shrink-0" />}
      {label}
    </button>
  )
}

export function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0">
      <legend className="mb-1.5 p-0 text-sm font-bold">{title}</legend>
      {children}
    </fieldset>
  )
}

/** The run a day-only screen has now, as the owner's choice. */
export const whenOf = (spec: ScreenRow['spec']): When =>
  spec.run.on === 'after_close' ? 'after_close' : 'during_day'

export function Problem({ words }: { words: string | null }) {
  if (!words) return null
  return (
    <p role="alert" className="flex items-start gap-1.5 text-sm font-semibold text-destructive">
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      {words}
    </p>
  )
}

const SORTS: Array<[WidgetSort['by'] | null, string]> = [
  [null, "The screen's own order"],
  ['change_pct', 'Change at the run'],
  ['ltp', 'Price at the run'],
  ['entered', 'Newest entered'],
  ['value', 'A value'],
]

/**
 * A widget's settings (design 3.9): the screen's full name (a long-press cannot be found on a
 * phone), its kind, rows, sort and the two values, and for a screen that reads day candles alone
 * when it checks, which is a normal edit of the screen. A choice is sent the moment it is tapped,
 * once at a time, against the dashboard's version; the dashboard is read again afterwards.
 */
export function WidgetSheet({
  card,
  screen,
  version,
  onClose,
}: {
  card: WidgetCard | null
  screen: ScreenRow | undefined
  version: number
  onClose: () => void
}) {
  const client = useQueryClient()
  const send = useSend()
  const refresh = () => void client.invalidateQueries({ queryKey: DASHBOARD_KEY })

  const change = (set: WidgetUpdate['set']) => {
    if (!card) return
    void send
      .run(async () => {
        const written = await guarded(client, card.screen_id, () =>
          screenerApi.widgetUpdate(card.screen_id, { version, kind: card.kind, set })
        )
        noteWritten(client, written)
        refresh()
      })
      .then((ok) => {
        if (ok && set.kind) onClose()
      })
  }

  const setWhen = (choice: When) => {
    if (!screen || whenOf(screen.spec) === choice) return
    void send.run(async () => {
      try {
        const next = await screenerApi.change(screen.id, {
          version: screen.version,
          action: 'edit',
          spec: { ...screen.spec, run: WHEN[choice] },
        })
        client.setQueryData<ScreenerState>(STATE_KEY, (state) =>
          state ? putRow(state, next) : state
        )
        refresh()
      } catch (error) {
        void client.invalidateQueries({ queryKey: STATE_KEY })
        throw error
      }
    })
  }

  const clauses = screen ? clausesOf(screen.spec.where).filter((clause) => clause.on) : []
  const values = card?.value_clauses ?? []
  const sort = card?.sort_by ?? null
  const kinds: Array<[WidgetKind, string]> = [
    ['table', 'Table'],
    ['count', 'Count'],
  ]
  return (
    <BottomSheet
      open={card !== null}
      onOpenChange={(next) => !next && onClose()}
      title="Widget settings"
    >
      {card && (
        <div className="flex flex-col gap-4">
          <p className="flex flex-col text-sm">
            <span className="font-medium text-[color:var(--text-secondary)]">Screen</span>
            <span className="font-bold break-words">{screen?.name ?? card.name}</span>
          </p>
          <Group title="Kind">
            <div className="flex gap-2">
              {kinds.map(([kind, label]) => (
                <Choice
                  key={kind}
                  label={label}
                  chosen={card.kind === kind}
                  disabled={send.pending}
                  onClick={() => card.kind !== kind && change({ kind })}
                />
              ))}
            </div>
          </Group>
          {card.kind === 'table' && (
            <>
              <Group title="Rows">
                <div className="flex gap-2">
                  {([5, 10, 20] as const).map((rows) => (
                    <Choice
                      key={rows}
                      label={`${rows} rows`}
                      chosen={card.rows === rows}
                      disabled={send.pending}
                      onClick={() => card.rows !== rows && change({ rows })}
                    />
                  ))}
                </div>
              </Group>
              <Group title="Sort">
                {SORTS.map(([by, label]) => (
                  <Choice
                    key={label}
                    wide
                    label={label}
                    chosen={(sort?.by ?? null) === by}
                    disabled={send.pending || (by === 'value' && clauses.length === 0)}
                    onClick={() => {
                      if (by === null) return change({ sort_by: null })
                      const order = sort?.order ?? 'desc'
                      change({
                        sort_by:
                          by === 'value'
                            ? { by, order, clause: values[0] ?? clauses[0]?.id }
                            : { by, order },
                      })
                    }}
                  />
                ))}
                {sort && (
                  <div className="flex gap-2">
                    {(['desc', 'asc'] as const).map((order) => (
                      <Choice
                        key={order}
                        label={order === 'desc' ? 'Highest first' : 'Lowest first'}
                        chosen={sort.order === order}
                        disabled={send.pending}
                        onClick={() =>
                          sort.order !== order && change({ sort_by: { ...sort, order } })
                        }
                      />
                    ))}
                  </div>
                )}
              </Group>
              {clauses.length > 0 && (
                <Group title="Values shown on each row (up to 2)">
                  {clauses.map((clause) => {
                    const on = values.includes(clause.id)
                    return (
                      <Choice
                        key={clause.id}
                        wide
                        label={clauseSentence(clause)}
                        chosen={on}
                        disabled={send.pending || (!on && values.length >= 2)}
                        onClick={() => {
                          const next = on
                            ? values.filter((id) => id !== clause.id)
                            : [...values, clause.id]
                          change({ value_clauses: next.length > 0 ? next : null })
                        }}
                      />
                    )
                  })}
                </Group>
              )}
            </>
          )}
          {card.day_only && screen && (
            <Group title="When it checks">
              {(Object.keys(WHEN) as When[]).map((choice) => (
                <Choice
                  key={choice}
                  wide
                  label={WHEN_WORDS[choice]}
                  chosen={whenOf(screen.spec) === choice}
                  disabled={send.pending}
                  onClick={() => setWhen(choice)}
                />
              ))}
            </Group>
          )}
          <Problem words={send.problem?.message ?? null} />
        </div>
      )}
    </BottomSheet>
  )
}
