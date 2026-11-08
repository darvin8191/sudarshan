import { useQuery, useQueryClient } from '@tanstack/react-query'
import { FlaskConical, LoaderCircle, Save, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { ACTION, OUTLINED } from '@/sudarshan/books/cards/parts'
import { toast } from '@/utils/toast'
import { screenerApi } from './api'
import { INDEX_LISTS, NAME_MAX, RUN_TIMEFRAMES } from './catalog'
import { STATE_KEY, UNIVERSES_KEY } from './keys'
import { PreviewSheet } from './PreviewSheet'
import { RuleBuilder } from './RuleBuilder'
import { putRow } from './stateCache'
import { clausesOf, countOn, freshClause, groupOf } from './tree'
import type {
  ScreenerState,
  ScreenRow,
  ScreenSpec,
  TimeFrame,
  Universe,
  UniverseOption,
} from './types'
import { WORDS } from './types'
import { usePreviewCheck } from './usePreviewCheck'
import type { Address } from './useScreenerAddress'
import { useSend } from './useSend'

const INPUT =
  'min-h-12 w-full rounded-xl border border-input bg-card px-3 text-[0.9375rem] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring'

/** A screen with nothing written yet: F&O stocks, each 15-minute close, one condition to fill in. */
export function blankSpec(): ScreenSpec {
  const where = groupOf('all', [])
  return {
    schema: 1,
    name: '',
    universe: { kind: 'fno_stocks' },
    run: { on: 'close', tf: 15 },
    where: groupOf('all', [freshClause(where, 15)]),
  }
}

/** The universe a choice stands for. */
export function universeOf(option: UniverseOption): Universe {
  switch (option.kind) {
    case 'options':
      return { kind: 'options', underlying: option.underlying ?? 'NIFTY' }
    case 'index_list':
      return { kind: 'index_list', id: option.id ?? 'NIFTY50' }
    case 'watchlist':
      return { kind: 'watchlist', list_id: option.list_id ?? 0 }
    default:
      return { kind: option.kind }
  }
}

const sameUniverse = (a: Universe, b: Universe) => JSON.stringify(a) === JSON.stringify(b)

/** The choices when the service has not listed any: the plain kinds and the shipped index lists. */
const FALLBACK: UniverseOption[] = [
  { kind: 'fno_stocks', label: 'F&O stocks', note: null },
  { kind: 'fno_futures', label: 'Stock futures', note: null },
  { kind: 'index_futures', label: 'Index futures', note: null },
  { kind: 'indices', label: 'Indices', note: null },
  ...INDEX_LISTS.map((list) => ({
    kind: 'index_list' as const,
    id: list.id,
    label: list.label,
    note: null,
  })),
]

export function runWords(run: ScreenSpec['run']): string {
  return run.on === 'after_close' ? 'After the close (16:05)' : `Each ${run.tf}-min candle close`
}

/** The tf a new condition starts on: the screen's own candles, or the day for an after-close screen. */
export const startTf = (run: ScreenSpec['run']): TimeFrame => (run.on === 'close' ? run.tf : 'D')

/**
 * The screen as it is written (design 5.2): its name, where it looks, when it checks, and its
 * conditions in the rule builder. The service checks the draft as the owner edits and its words
 * show beside the condition they are about. Save is sent once, with the screen's version when
 * it is an edit: two phones editing one screen get "This screen was changed on another screen.
 * Open it again, then edit it."
 */
export function ScreenEditor({
  row,
  address,
  openSheet,
  closeSheet,
  onSaved,
}: {
  /** The saved screen being edited; absent for a new one. */
  row?: ScreenRow
  address: Address
  openSheet: (sheet: string, extra?: Record<string, string>) => void
  closeSheet: () => void
  onSaved: (row: ScreenRow) => void
}) {
  const client = useQueryClient()
  const [draft, setDraft] = useState<ScreenSpec>(() => row?.spec ?? blankSpec())
  // What the draft was started from: a newer version elsewhere stops Save once the draft is touched.
  const [base, setBase] = useState(() => ({ spec: row?.spec ?? null, version: row?.version ?? 0 }))
  const requestId = useRef(crypto.randomUUID())
  const send = useSend()
  const universes = useQuery({
    queryKey: UNIVERSES_KEY,
    queryFn: screenerApi.universes,
    staleTime: 5 * 60_000,
    retry: false,
  })
  const options = universes.data?.universes.length ? universes.data.universes : FALLBACK

  const touched = JSON.stringify(draft) !== JSON.stringify(base.spec)
  const dirty = row ? JSON.stringify(draft) !== JSON.stringify(row.spec) : true
  const changedElsewhere = row !== undefined && row.version !== base.version && touched
  const check = usePreviewCheck(draft, !changedElsewhere)

  // A newer version of the screen takes the place of a draft that was never touched.
  useEffect(() => {
    if (!row || row.version === base.version || touched) return
    setBase({ spec: row.spec, version: row.version })
    setDraft(row.spec)
  }, [row, base.version, touched])

  const problems = useMemo(() => {
    const out = new Map<string, string>()
    const problem =
      check.state.status === 'problem'
        ? check.state.problem
        : send.problem?.code === 'invalid'
          ? send.problem
          : null
    if (problem?.field) {
      const id = clausesOf(draft.where).find((clause) => clause.id === problem.field)?.id
      if (id) out.set(id, problem.message)
    }
    return out
  }, [check.state, send.problem, draft.where])

  const general =
    check.state.status === 'problem' && !problems.size
      ? check.state.problem.message
      : send.problem && !(send.problem.code === 'invalid' && problems.size)
        ? send.problem.code === 'unanswered'
          ? WORDS.not_confirmed
          : send.problem.code === 'conflict'
            ? WORDS.conflict
            : send.problem.message
        : null

  const set = (change: Partial<ScreenSpec>) => {
    send.clear()
    setDraft((held) => ({ ...held, ...change }))
  }
  const selected = options.findIndex((option) => sameUniverse(universeOf(option), draft.universe))
  const note = selected >= 0 ? options[selected].note : null
  const noneOn = countOn(draft.where) === 0

  const save = async () => {
    let saved: ScreenRow | null = null
    const ok = await send.run(async () => {
      saved = row
        ? await screenerApi.change(row.id, { version: row.version, spec: draft })
        : await screenerApi.create({ spec: draft, request_id: requestId.current })
    })
    if (ok && saved) {
      const next: ScreenRow = saved
      setBase({ spec: next.spec, version: next.version })
      setDraft(next.spec)
      client.setQueryData<ScreenerState>(STATE_KEY, (state) =>
        state ? putRow(state, next) : state
      )
      toast.success('Screen saved.')
      onSaved(next)
    } else if (send.last.current?.code === 'conflict') {
      void client.invalidateQueries({ queryKey: STATE_KEY })
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <section className="flex flex-col gap-3 rounded-[1.125rem] border bg-card p-3 shadow-[var(--shadow-card)]">
        <label className="flex flex-col gap-1 text-sm font-semibold">
          Name
          <input
            type="text"
            value={draft.name}
            maxLength={NAME_MAX}
            onChange={(event) => set({ name: event.target.value })}
            className={INPUT}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-semibold">
          Where it looks
          <select
            value={selected}
            onChange={(event) => {
              const option = options[Number(event.target.value)]
              if (option) set({ universe: universeOf(option) })
            }}
            className={INPUT}
          >
            {selected < 0 && <option value={-1}>Your list</option>}
            {options.map((option, index) => (
              <option
                key={`${option.kind}:${option.id ?? option.list_id ?? option.underlying ?? ''}`}
                value={index}
              >
                {option.label}
              </option>
            ))}
          </select>
          {note && (
            <span className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
              {note}
            </span>
          )}
        </label>
        <fieldset className="flex flex-col gap-1.5">
          <legend className="pb-1 text-sm font-semibold">When it checks</legend>
          <div className="flex flex-wrap gap-1.5">
            {RUN_TIMEFRAMES.map((tf) => {
              const on = draft.run.on === 'close' && draft.run.tf === tf
              return (
                <button
                  key={tf}
                  type="button"
                  aria-pressed={on}
                  onClick={() => set({ run: { on: 'close', tf } })}
                  className={cn(
                    OUTLINED,
                    on && 'border-primary bg-[color:var(--brand-tint)] font-bold text-primary'
                  )}
                >
                  {tf} min
                </button>
              )
            })}
            <button
              type="button"
              aria-pressed={draft.run.on === 'after_close'}
              onClick={() => set({ run: { on: 'after_close' } })}
              className={cn(
                OUTLINED,
                draft.run.on === 'after_close' &&
                  'border-primary bg-[color:var(--brand-tint)] font-bold text-primary'
              )}
            >
              After the close
            </button>
          </div>
          <p className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
            {runWords(draft.run)}
          </p>
        </fieldset>
      </section>

      <RuleBuilder
        where={draft.where}
        onChange={(where) => set({ where })}
        runTf={startTf(draft.run)}
        problems={problems}
        address={address}
        openSheet={openSheet}
        closeSheet={closeSheet}
      />

      {noneOn && (
        <p className="flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]">
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          Turn on at least one condition.
        </p>
      )}
      {changedElsewhere && (
        <p
          role="alert"
          className="flex items-start gap-1.5 text-sm font-semibold text-[color:var(--warning-text)]"
        >
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {WORDS.conflict}
        </p>
      )}
      {general && (
        <p role="alert" className="flex items-start gap-1.5 text-sm font-semibold text-destructive">
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {general}
        </p>
      )}
      {check.state.status === 'checking' && (
        <p className="flex items-center gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Checking what you wrote
        </p>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => {
            check.now()
            openSheet('preview')
          }}
          disabled={changedElsewhere}
          className={cn(OUTLINED, 'flex-1')}
        >
          <FlaskConical aria-hidden="true" className="size-5" />
          Test on the last candle
        </button>
        <button
          type="button"
          disabled={
            send.pending || changedElsewhere || !draft.name.trim() || (row !== undefined && !dirty)
          }
          onClick={() => void save()}
          className={cn(ACTION, 'flex-1 bg-primary text-primary-foreground hover:bg-primary/90')}
        >
          <Save aria-hidden="true" className="size-5" />
          {send.pending ? 'Saving' : 'Save'}
        </button>
      </div>

      <PreviewSheet open={address.sheet === 'preview'} state={check.state} onClose={closeSheet} />
    </div>
  )
}
