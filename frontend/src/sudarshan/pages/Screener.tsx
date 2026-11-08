// SUDARSHAN M9.3: the Screener page, phone first. The owner's screens as the screener service
// holds them (blueprints/sudarshan_screener.py, design 6.3), kept current by what it pushes
// (screener/push.ts). What is open lives in the address (screener/useScreenerAddress.ts), so the
// phone's Back closes a sheet first, then the screen.
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, LoaderCircle, RefreshCw } from 'lucide-react'
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
import { droppedHere } from '@/sudarshan/screener/actions'
import { DeleteSheet } from '@/sudarshan/screener/DeleteSheet'
import { EngineText } from '@/sudarshan/screener/marks'
import { useHearing, useScreenerPush } from '@/sudarshan/screener/push'
import { ScreenCard } from '@/sudarshan/screener/ScreenCard'
import { ScreenPage } from '@/sudarshan/screener/ScreenPage'
import { type ScreenRow, WORDS } from '@/sudarshan/screener/types'
import { useScreenerAddress } from '@/sudarshan/screener/useScreenerAddress'
import { useScreenerState } from '@/sudarshan/screener/useScreenerState'
import { toast } from '@/utils/toast'

const NO_ROWS: ScreenRow[] = []

/** In place of the engine line while this screen is not in the owner's room. */
const NOT_RECEIVING = {
  state: 'feed_down',
  line: 'Not receiving updates on this screen · reconnecting',
  real_time: false,
} as const

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
  const { address, openScreen, closeScreen, openSheet, closeSheet } = useScreenerAddress()
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
      {open === null && (
        <div className="flex items-center justify-between gap-3">
          <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">
            Screener
          </h1>
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
        ) : state && hearing === 'not_joined' ? (
          <span data-heard="no" className="inline-flex">
            <EngineText line={NOT_RECEIVING} />
          </span>
        ) : state ? (
          <span data-heard="yes" className="inline-flex">
            <EngineText line={state.engine} className={cn(!state.engine.real_time && undefined)} />
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

      {open ? (
        <ScreenPage row={open} onBack={closeScreen} onDelete={() => openSheet('delete')} />
      ) : (
        <section className={cn('flex flex-col gap-2', problem && state && STALE)}>
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
        </section>
      )}

      <DeleteSheet row={address.sheet === 'delete' ? open : null} onClose={closeSheet} />
    </div>
  )
}
