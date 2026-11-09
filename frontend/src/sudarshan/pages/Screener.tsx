// SUDARSHAN M9.3: the Screener page, phone first. The owner's screens as the screener service
// holds them (blueprints/sudarshan_screener.py, design 6.3), kept current by what it pushes
// (screener/push.ts). What is open lives in the address (screener/useScreenerAddress.ts), so the
// phone's Back closes a sheet first, then the screen.
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, Gauge, LoaderCircle, Plus, RefreshCw } from 'lucide-react'
import { useEffect, useMemo } from 'react'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { cn } from '@/lib/utils'
import {
  EmptyCard,
  LoadingCards,
  OUTLINED,
  STALE,
  StaleBookNote,
} from '@/sudarshan/books/cards/parts'
import { istTime } from '@/sudarshan/books/time'
import { droppedHere } from '@/sudarshan/screener/actions'
import { Dashboard } from '@/sudarshan/screener/Dashboard'
import { DeleteSheet } from '@/sudarshan/screener/DeleteSheet'
import { History } from '@/sudarshan/screener/History'
import { Library } from '@/sudarshan/screener/Library'
import { EngineText } from '@/sudarshan/screener/marks'
import { useHearing, useScreenerPush } from '@/sudarshan/screener/push'
import { ResultsPanel } from '@/sudarshan/screener/ResultsPanel'
import { RunSheet } from '@/sudarshan/screener/RunSheet'
import { ScreenCard } from '@/sudarshan/screener/ScreenCard'
import { ScreenEditor } from '@/sudarshan/screener/ScreenEditor'
import { type PanelTab, ScreenPage } from '@/sudarshan/screener/ScreenPage'
import {
  type EngineLine,
  type ScreenerState,
  type ScreenRow,
  WORDS,
} from '@/sudarshan/screener/types'
import { type HomeTab, useScreenerAddress } from '@/sudarshan/screener/useScreenerAddress'
import { useScreenerState } from '@/sudarshan/screener/useScreenerState'
import { toast } from '@/utils/toast'

const NO_ROWS: ScreenRow[] = []

/** In place of the engine line while this screen is not in the owner's room. */
const NOT_RECEIVING = {
  state: 'feed_down',
  line: 'Not receiving updates on this screen · reconnecting',
  real_time: false,
} as const

/** ' · next check 10:45' when the service sent the time and its line does not already say so. */
export function nextCheckWords(engine: EngineLine): string {
  if (!engine.next_at || /next check/i.test(engine.line)) return ''
  return ` · next check ${istTime(engine.next_at).slice(0, 5)}`
}

/** How much of the screener's room the owner's screens use, in words: screens and instruments. */
function Budget({ limits }: { limits: ScreenerState['limits'] }) {
  return (
    <p
      data-budget="true"
      className="flex items-start gap-1.5 px-1 pt-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]"
    >
      <Gauge aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>
        {limits.active_used} of {limits.active} screens checking through the day
        {limits.max_keys
          ? ` · prices followed for up to ${limits.max_keys} instruments at once`
          : ''}
      </span>
    </p>
  )
}

/** The panels of an open screen. */
const TABS: PanelTab[] = [
  { id: 'results', label: 'Results' },
  { id: 'history', label: 'History' },
  { id: 'edit', label: 'Edit' },
]

/** The Dashboard, Screens and the Library: what the home of the Screener holds. */
function HomeTabs({ home, onTab }: { home: HomeTab; onTab: (tab: HomeTab) => void }) {
  return (
    <div role="tablist" aria-label="Screener" className="flex items-center gap-1.5">
      {(
        [
          ['dashboard', 'Dashboard'],
          ['screens', 'Screens'],
          ['library', 'Library'],
        ] as const
      ).map(([id, label]) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={id === home}
          onClick={() => onTab(id)}
          className="group flex h-12 shrink-0 items-center outline-none"
        >
          <span
            className={cn(
              'flex h-9 items-center rounded-full border px-3.5 text-sm group-focus-visible:ring-2 group-focus-visible:ring-ring',
              id === home
                ? 'border-primary bg-[color:var(--primary-tint)] font-bold text-primary'
                : 'border-border bg-card font-semibold text-[color:var(--text-secondary)]'
            )}
          >
            {label}
          </span>
        </button>
      ))}
    </div>
  )
}

const ORDER = { active: 0, paused: 1, stopped: 2 } as const

/** Active first, then Paused, then Stopped, newest first in each. */
export function sorted(rows: ScreenRow[]): ScreenRow[] {
  return [...rows].sort((a, b) => ORDER[a.status] - ORDER[b.status] || b.id - a.id)
}

export default function Screener() {
  const client = useQueryClient()
  const { socket } = useSocketContext()
  const query = useScreenerState()
  const state = query.data
  const { address, openScreen, openDraft, closeScreen, openSheet, closeSheet, setTab } =
    useScreenerAddress()
  useScreenerPush(client, socket)
  const hearing = useHearing()

  const screens = state?.screens ?? NO_ROWS
  const list = useMemo(() => sorted(screens), [screens])
  const open =
    address.screen === null ? null : (screens.find((row) => row.id === address.screen) ?? null)

  // A screen that cannot be shown (deleted meanwhile) is closed once the screens are known. One
  // that went on another screen says so; one deleted here has said so already.
  const orphan = state !== undefined && address.screen !== null && open === null
  useEffect(() => {
    if (!orphan || address.screen === null) return
    if (!droppedHere(address.screen)) toast.info(WORDS.gone)
    closeScreen()
  }, [orphan, address.screen, closeScreen])

  const problem = query.error
  const switchedOff = state !== undefined && !state.enabled
  return (
    <div className="flex flex-col gap-2">
      {open === null && !address.draft && (
        <div className="flex items-center justify-between gap-3">
          <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">
            Screener
          </h1>
          <button
            type="button"
            disabled={!state?.enabled}
            onClick={openDraft}
            className="inline-flex min-h-12 items-center gap-1.5 rounded-[0.875rem] bg-primary px-4 text-[0.9375rem] font-semibold text-primary-foreground outline-none hover:bg-primary/90 focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:bg-muted disabled:text-[color:var(--text-tertiary)] disabled:shadow-[inset_0_0_0_1px_var(--border-strong)]"
          >
            <Plus aria-hidden="true" className="size-5" />
            New
          </button>
        </div>
      )}

      <div className="flex min-h-8 flex-wrap items-center gap-x-3 gap-y-1 px-1">
        {problem ? (
          <span className="inline-flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]">
            <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {problem.code === 'unanswered' || problem.code === 'failed'
              ? WORDS.not_answering
              : problem.message}
          </span>
        ) : state?.engine.real_time && hearing !== 'joined' ? (
          <span data-heard="no" className="inline-flex">
            <EngineText line={NOT_RECEIVING} />
          </span>
        ) : state ? (
          <span data-heard="yes" className="inline-flex">
            <EngineText
              line={{ ...state.engine, line: state.engine.line + nextCheckWords(state.engine) }}
            />
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            Reading your screens
          </span>
        )}
        {problem && (
          <button
            type="button"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
            className={OUTLINED}
          >
            <RefreshCw
              aria-hidden="true"
              className={cn('size-4', query.isFetching && 'animate-spin')}
            />
            Try again
          </button>
        )}
      </div>
      {problem && state && <StaleBookNote text="These are your screens as last read." />}

      {address.draft && state?.enabled ? (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Back to screens"
              onClick={closeScreen}
              className={cn(OUTLINED, 'w-12 shrink-0 px-0')}
            >
              <ArrowLeft aria-hidden="true" className="size-5" />
            </button>
            <h1 className="sd-display text-[1.5rem] leading-tight font-bold">New screen</h1>
          </div>
          <ScreenEditor
            address={address}
            openSheet={openSheet}
            closeSheet={closeSheet}
            onSaved={(row) => openScreen(row.id)}
          />
        </div>
      ) : open ? (
        <ScreenPage
          row={open}
          tabs={TABS}
          tab={address.tab}
          onTab={setTab}
          onBack={closeScreen}
          onDelete={() => openSheet('delete')}
        >
          {address.tab === 'edit' ? (
            <ScreenEditor
              key={open.id}
              row={open}
              address={address}
              openSheet={openSheet}
              closeSheet={closeSheet}
              onSaved={() => {}}
            />
          ) : address.tab === 'history' ? (
            <>
              <History
                screenId={open.id}
                hearing={hearing}
                onOpen={(runId) => openSheet('run', { run: String(runId) })}
              />
              <RunSheet runId={address.sheet === 'run' ? address.run : null} onClose={closeSheet} />
            </>
          ) : (
            <ResultsPanel
              key={open.id}
              screen={open}
              address={address}
              hearing={hearing}
              openSheet={openSheet}
              closeSheet={closeSheet}
            />
          )}
        </ScreenPage>
      ) : address.home === 'dashboard' ? (
        <section className="flex flex-col gap-2">
          <HomeTabs home={address.home} onTab={setTab} />
          {!state ? (
            query.isPending ? (
              <LoadingCards />
            ) : null
          ) : switchedOff ? (
            <EmptyCard title="Nothing is being screened" hint={WORDS.switched_off} />
          ) : (
            <Dashboard
              address={address}
              hearing={hearing}
              openSheet={openSheet}
              closeSheet={closeSheet}
            />
          )}
        </section>
      ) : address.home === 'library' ? (
        <section className="flex flex-col gap-2">
          <HomeTabs home={address.home} onTab={setTab} />
          <Library onMade={(row) => openScreen(row.id)} />
        </section>
      ) : (
        <section className={cn('flex flex-col gap-2', problem && state && STALE)}>
          <HomeTabs home={address.home} onTab={setTab} />
          {!state ? (
            query.isPending ? (
              <LoadingCards />
            ) : null
          ) : switchedOff ? (
            <EmptyCard title="Nothing is being screened" hint={WORDS.switched_off} />
          ) : list.length === 0 ? (
            <EmptyCard
              title="No screens yet"
              hint="A screen checks a set of instruments against conditions you write, and tells you what matches."
            />
          ) : (
            <ul className="flex flex-col gap-2">
              {list.map((row) => (
                <li key={row.id}>
                  <ScreenCard row={row} onOpen={(id) => openScreen(id)} />
                </li>
              ))}
            </ul>
          )}
          {state?.enabled && <Budget limits={state.limits} />}
        </section>
      )}

      <DeleteSheet row={address.sheet === 'delete' ? open : null} onClose={closeSheet} />
    </div>
  )
}
