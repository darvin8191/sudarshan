import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, LoaderCircle, Plus } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { ACTION } from '@/sudarshan/books/cards/parts'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { MAX_LISTS, readLists, WATCHLISTS_KEY } from '@/sudarshan/watchlist/useWatchlists'
import { toast } from '@/utils/toast'
import { screenerApi } from './api'
import { cardKey } from './dashboardCache'
import { guarded, noteWritten, pausedAddWords } from './dashboardWrites'
import { DASHBOARD_KEY, STATE_KEY } from './keys'
import { usePresets } from './Library'
import { putRow } from './stateCache'
import { clausesOf } from './tree'
import type {
  Clause,
  Operand,
  PresetCard,
  ScreenerState,
  ScreenRow,
  ScreenSpec,
  WidgetAdd,
  WidgetCard,
  WidgetKind,
} from './types'
import { useSend } from './useSend'
import { Choice, Group, Problem, WHEN, WHEN_WORDS, type When, whenOf } from './WidgetSheet'
import { clauseSentence } from './words'

export const LISTS_USED_UP =
  "OpenAlgo holds at most 50 watchlists and all 50 are in use. Choose one of your lists for this screen's matches, or delete a list first."

const leaves = (operand: Operand): Operand[] =>
  'math' in operand ? operand.args.flatMap(leaves) : [operand]

/**
 * Whether a screen reads only day candles and session levels, from what it says: every line it
 * reads is on the day's candle. The service decides in the end (it refuses a choice for any
 * other screen in its own words); this only decides whether to offer "When it checks".
 */
export function dayOnlySpec(spec: ScreenSpec): boolean {
  const used: Operand[] = [
    ...clausesOf(spec.where).flatMap((clause: Clause) => [
      clause.left,
      clause.right ?? { num: '0' },
    ]),
    ...(spec.rank ? [spec.rank.by] : []),
  ].flatMap(leaves)
  const read = used.filter((operand) => !('num' in operand))
  return read.length > 0 && read.every((operand) => 'tf' in operand && operand.tf === 'D')
}

type Source = { type: 'screen'; id: number } | { type: 'preset'; key: string }

function Body({
  state,
  cards,
  version,
  onClose,
}: {
  state: ScreenerState
  cards: readonly WidgetCard[]
  version: number
  onClose: () => void
}) {
  const client = useQueryClient()
  const send = useSend()
  const requestId = useRef(crypto.randomUUID())
  /** Set once the widget is added: a tap that lands after the answer sends nothing more. */
  const added = useRef(false)
  const [tab, setTab] = useState<'screens' | 'library'>('screens')
  const [kind, setKind] = useState<WidgetKind>('table')
  const [rows, setRows] = useState<5 | 10 | 20>(10)
  const [pick, setPick] = useState<Source | null>(null)
  const [when, setWhen] = useState<When | null>(null)
  const [start, setStart] = useState(true)
  const [listId, setListId] = useState<number | null>(null)

  const presets = usePresets()
  const lists = useQuery({
    queryKey: WATCHLISTS_KEY,
    queryFn: readLists,
    staleTime: 15_000,
    retry: false,
    enabled: pick?.type === 'preset',
  })

  const onDash = useMemo(() => new Set(cards.map(cardKey)), [cards])
  const free = state.screens.filter((row) => !onDash.has(`${row.id}:${kind}`))
  const screen: ScreenRow | undefined =
    pick?.type === 'screen' ? state.screens.find((row) => row.id === pick.id) : undefined
  const preset: PresetCard | undefined =
    pick?.type === 'preset'
      ? presets.data?.presets.find((card) => card.key === pick.key)
      : undefined
  const spec = screen?.spec ?? preset?.spec ?? null

  const dayOnly = spec !== null && dayOnlySpec(spec)
  const chosen: When | null = dayOnly && spec ? (when ?? whenOf(spec)) : null
  const checksAllDay = spec !== null && (chosen ? chosen === 'during_day' : spec.run.on === 'close')
  const { active, active_used } = state.limits
  const blocked = checksAllDay && active_used >= active && screen?.status !== 'active'
  const startable = screen?.status !== 'active'
  const needsList =
    preset !== undefined && spec?.target != null && (lists.data?.length ?? 0) >= MAX_LISTS
  const ready = spec !== null && !send.pending && (!needsList || listId !== null)

  const choose = (next: Source) => {
    setPick(next)
    setWhen(null)
    setListId(null)
    send.clear()
  }

  const add = () => {
    if (!spec || !pick || added.current) return
    void send
      .run(async () => {
        const body: WidgetAdd = {
          version,
          kind,
          ...(kind === 'table' ? { rows } : {}),
          start: startable && start && !blocked,
          ...(when && when !== whenOf(spec) ? { when } : {}),
        }
        let screenId: number | null = null
        if (pick.type === 'screen') {
          body.screen_id = screenId = pick.id
        } else if (needsList && spec.target) {
          // The service never makes a 51st list: the screen is made on a list the owner chose.
          const made = await screenerApi.create({
            spec: { ...spec, target: { ...spec.target, list_id: listId, create_name: null } },
            request_id: requestId.current,
            status: 'paused',
          })
          client.setQueryData<ScreenerState>(STATE_KEY, (held) =>
            held ? putRow(held, made) : held
          )
          body.screen_id = screenId = made.id
        } else {
          body.preset = pick.key
        }
        const written = await guarded(client, screenId, () => screenerApi.widgetAdd(body))
        client.setQueryData<ScreenerState>(STATE_KEY, (held) =>
          held ? putRow(held, written.screen) : held
        )
        noteWritten(client, written)
        void client.invalidateQueries({ queryKey: DASHBOARD_KEY })
        toast.success(written.notice ?? 'Added to the dashboard.')
      })
      .then((ok) => {
        if (!ok) return
        added.current = true
        onClose()
      })
  }

  return (
    <div className="flex flex-col gap-4">
      <Group title="Kind">
        <div className="flex gap-2">
          {(['table', 'count'] as const).map((each) => (
            <Choice
              key={each}
              label={each === 'table' ? 'Table' : 'Count'}
              chosen={kind === each}
              onClick={() => {
                setKind(each)
                setPick(null)
              }}
            />
          ))}
        </div>
      </Group>
      <div role="tablist" aria-label="Where the screen comes from" className="flex gap-2">
        {(
          [
            ['screens', 'Your screens'],
            ['library', 'Library'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={cn(
              ACTION,
              'flex-1 border border-input',
              tab === id && 'border-primary bg-[color:var(--brand-tint)] font-bold text-primary'
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'screens' ? (
        free.length === 0 ? (
          <p className="text-sm font-medium text-[color:var(--text-secondary)]">
            Every screen is already on the dashboard as a {kind}.
          </p>
        ) : (
          <Group title="Screens">
            {free.map((row) => (
              <Choice
                key={row.id}
                wide
                label={`${row.name} · ${row.where}`}
                chosen={pick?.type === 'screen' && pick.id === row.id}
                onClick={() => choose({ type: 'screen', id: row.id })}
              />
            ))}
          </Group>
        )
      ) : presets.data ? (
        <Group title="Ready-made screens">
          {presets.data.presets.map((card) =>
            card.usable ? (
              <Choice
                key={card.key}
                wide
                label={`${card.name}${card.where ? ` · ${card.where}` : ''}`}
                chosen={pick?.type === 'preset' && pick.key === card.key}
                onClick={() => choose({ type: 'preset', key: card.key })}
              />
            ) : (
              <p
                key={card.key}
                data-unavailable="true"
                className="flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]"
              >
                <Ban aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                {card.name}: {card.reason ?? 'Not available yet.'}
              </p>
            )
          )}
        </Group>
      ) : (
        <p className="text-sm font-medium text-[color:var(--text-secondary)]">
          Reading the ready-made screens
        </p>
      )}
      {spec && (
        <>
          <Group title="What it checks">
            <ul className="flex list-disc flex-col gap-1 pl-5 text-[0.875rem] font-medium">
              {clausesOf(spec.where)
                .filter((clause) => clause.on)
                .map((clause) => (
                  <li key={clause.id}>{clauseSentence(clause)}</li>
                ))}
            </ul>
          </Group>
          {kind === 'table' && (
            <Group title="Rows">
              <div className="flex gap-2">
                {([5, 10, 20] as const).map((each) => (
                  <Choice
                    key={each}
                    label={`${each} rows`}
                    chosen={rows === each}
                    onClick={() => setRows(each)}
                  />
                ))}
              </div>
            </Group>
          )}
          {chosen && (
            <Group title="When it checks">
              {(Object.keys(WHEN) as When[]).map((each) => (
                <Choice
                  key={each}
                  wide
                  label={WHEN_WORDS[each]}
                  chosen={chosen === each}
                  onClick={() => setWhen(each)}
                />
              ))}
            </Group>
          )}
          {startable && (
            <Group title="Start checking">
              <Choice
                wide
                label={`Start checking after adding · ${start && !blocked ? 'on' : 'off'}`}
                chosen={start && !blocked}
                disabled={blocked}
                onClick={() => setStart(!start)}
              />
              {blocked && (
                <p className="text-[0.8125rem] font-medium text-[color:var(--warning-text)]">
                  {pausedAddWords(active_used, active)}
                </p>
              )}
            </Group>
          )}
          {needsList && lists.data && (
            <div className="flex flex-col gap-1.5">
              <p className="text-sm font-semibold">{LISTS_USED_UP}</p>
              <select
                aria-label="List for this screen's matches"
                value={listId ?? ''}
                onChange={(event) =>
                  setListId(event.target.value ? Number(event.target.value) : null)
                }
                className="min-h-12 w-full rounded-xl border border-input bg-card px-3 text-[0.9375rem] font-semibold"
              >
                <option value="">Choose a list</option>
                {lists.data.map((list) => (
                  <option key={list.id} value={list.id}>
                    {list.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </>
      )}
      <Problem words={send.problem?.message ?? null} />
      <button
        type="button"
        disabled={!ready}
        onClick={add}
        className={cn(ACTION, 'bg-primary text-primary-foreground hover:bg-primary/90')}
      >
        {send.pending ? (
          <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
        ) : (
          <Plus aria-hidden="true" className="size-5" />
        )}
        {send.pending ? 'Adding' : 'Add to dashboard'}
      </button>
    </div>
  )
}

/** The Add a widget sheet (design 3.9, 3.10): a screen of yours, or a ready-made one, as a table or a count. */
export function AddWidgetSheet({
  open,
  state,
  cards,
  version,
  onClose,
}: {
  open: boolean
  state: ScreenerState
  cards: readonly WidgetCard[]
  version: number
  onClose: () => void
}) {
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Add a widget"
      description="Choose a screen to show on the dashboard"
    >
      <Body state={state} cards={cards} version={version} onClose={onClose} />
    </BottomSheet>
  )
}
