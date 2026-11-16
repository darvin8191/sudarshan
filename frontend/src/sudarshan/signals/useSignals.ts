// SUDARSHAN M13.4-M13.9 (P8 U1): the TRINETRA view's hooks. `useSignals` is the one every screen
// uses: the `state` answer read through TanStack Query at mount and after every reconnect, kept
// current by the push (live.ts), and carried on by the clock between answers (the header's age,
// going stale, the colour going at 30 minutes, a reading lapsing), each worked out again at the
// moment it changes rather than on a timer. The rest read the timeline, the settings and the score.
import {
  type QueryClient,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useSocketContext } from '@/components/socket/SocketProvider'
import {
  problemOf,
  retryable,
  SignalsError,
  type SignalsProblem,
  signalsCall,
  signalsData,
} from './api'
import { PERFORMANCE_KEY, SETTINGS_KEY, STATE_KEY, TIMELINE_KEY } from './keys'
import {
  asState,
  type Hearing,
  holdPush,
  newer,
  pushCount,
  type SignalsView,
  useSignalsHearing,
} from './live'
import {
  INSTRUMENTS,
  KNOWN_MODULES,
  type Lang,
  type Performance,
  type Reading,
  type Settings,
  type State,
  TIMELINE,
  type TimelinePage,
  WORDS,
} from './types'
import { type FreshNow, freshnessAt } from './words'

/** Reads again at most twice, and only where that can help. */
const retry = (count: number, error: unknown) => count < 2 && retryable(error)

/** One read of `state`, never replacing a newer answer already shown. */
export async function readState(client: QueryClient, signal?: AbortSignal): Promise<SignalsView> {
  const before = pushCount()
  let incoming: SignalsView
  try {
    const result = await signalsCall('state', undefined, signal)
    if (result.enabled) {
      const state = asState(result.data)
      if (!state) throw new SignalsError({ code: 'unreadable', message: WORDS.failed, field: null })
      incoming = { kind: 'state', state, at: Date.now() }
    } else {
      incoming = { kind: 'off', message: result.message, at: Date.now() }
    }
  } catch (error) {
    // A push arrived while this read was on its way: the service answers, and the push is newer.
    const shown = client.getQueryData<SignalsView>(STATE_KEY)
    if (shown?.kind === 'state' && pushCount() !== before) return shown
    throw error
  }
  const shown = client.getQueryData<SignalsView>(STATE_KEY)
  return shown && !newer(shown, incoming, pushCount() !== before) ? shown : incoming
}

/** Whether a reading has lapsed by now: the service said so, or its `valid_until` has passed. */
export function lapsedAt(reading: Reading, now: number): boolean {
  return reading.lapsed || (reading.valid_until_ms !== null && now >= reading.valid_until_ms)
}

const MODULE_ORDER = new Map<string, number>(KNOWN_MODULES.map((module, at) => [module, at]))
const INSTRUMENT_ORDER = new Map<string, number>(INSTRUMENTS.map((name, at) => [name, at]))

/** The readings in the order a screen lists them: live ones first, by module and index; lapsed below. */
export function orderReadings(readings: readonly Reading[], now: number): Reading[] {
  const module = (reading: Reading) => MODULE_ORDER.get(reading.module) ?? KNOWN_MODULES.length
  const index = (reading: Reading) => INSTRUMENT_ORDER.get(reading.instrument) ?? INSTRUMENTS.length
  return [...readings].sort(
    (a, b) =>
      Number(lapsedAt(a, now)) - Number(lapsedAt(b, now)) ||
      module(a) - module(b) ||
      a.module.localeCompare(b.module) ||
      index(a) - index(b)
  )
}

/** The longest a screen waits before working its words out again (a phone that slept is caught by its wake). */
const LONGEST_WAIT_MS = 6 * 3_600_000

export interface SignalsNow {
  /** The answer shown: a state, the view switched off, or nothing read yet. */
  view: SignalsView | undefined
  state: State | null
  /** The service's sentence while the view is off or not set up. */
  off: string | null
  /** Why the last read failed, in the owner's words (what was read before stays shown). */
  problem: SignalsProblem | null
  pending: boolean
  /** The header as it stands now. */
  fresh: FreshNow | null
  /** The server's clock now, as near as this phone can tell. */
  now: number
  hearing: Hearing
  /** A socket to hear pushes on is there at all. */
  connected: boolean
  refetch: () => void
}

/**
 * The view as it stands now. `moments` names further instants (server clock) at which a screen
 * changes by the clock alone, such as the edges of the gap-bias window.
 */
export function useSignals(moments?: (state: State, now: number) => number[]): SignalsNow {
  const client = useQueryClient()
  const { socket } = useSocketContext()
  const query = useQuery({
    queryKey: STATE_KEY,
    queryFn: ({ signal }) => readState(client, signal),
    retry,
    staleTime: 30_000,
  })
  const view = query.data
  // The room is joined unless the view is known to be off: a service that comes back pushes.
  const listening = view?.kind !== 'off' ? socket : null
  useEffect(() => (listening ? holdPush(client, listening) : undefined), [client, listening])
  const hearing = useSignalsHearing()

  const [, setTick] = useState(0)
  const state = view?.kind === 'state' ? view.state : null
  const offset = view?.kind === 'state' ? view.state.now_ms - view.at : 0
  const now = Date.now() + offset
  const fresh = state ? freshnessAt(state.freshness, now) : null
  const ahead = [
    fresh?.nextMs ?? null,
    ...(state?.readings ?? []).map((each) => (each.lapsed ? null : each.valid_until_ms)),
    ...(state && moments ? moments(state, now) : []),
  ].filter((at): at is number => typeof at === 'number' && at > now)
  const next = ahead.length > 0 ? Math.min(...ahead) : null

  useEffect(() => {
    if (next === null) return
    const wait = Math.min(LONGEST_WAIT_MS, Math.max(250, next - (Date.now() + offset)))
    const timer = setTimeout(() => setTick((tick) => tick + 1), wait)
    return () => clearTimeout(timer)
  }, [next, offset])
  useEffect(() => {
    const shown = () => {
      if (document.visibilityState === 'visible') setTick((tick) => tick + 1)
    }
    document.addEventListener('visibilitychange', shown)
    return () => document.removeEventListener('visibilitychange', shown)
  }, [])

  return {
    view,
    state,
    off: view?.kind === 'off' ? view.message : null,
    problem: query.isError ? problemOf(query.error) : null,
    pending: query.isPending,
    fresh,
    now,
    hearing,
    connected: socket !== null,
    refetch: () => void query.refetch(),
  }
}

/** The timeline, newest first, 50 a page; `fetchNextPage` reads the older page. */
export function useTimeline(enabled: boolean) {
  return useInfiniteQuery({
    queryKey: TIMELINE_KEY,
    queryFn: ({ pageParam, signal }): Promise<TimelinePage> =>
      signalsData(
        'timeline',
        pageParam === null ? { limit: TIMELINE.page } : { before: pageParam, limit: TIMELINE.page },
        signal
      ),
    initialPageParam: null as number | null,
    getNextPageParam: (last: TimelinePage) =>
      typeof last.next_before === 'number' ? last.next_before : undefined,
    enabled,
    retry,
  })
}

/** The score of every module (M13.8): computed nightly, so read when the tab opens and kept a while. */
export function usePerformance(enabled: boolean) {
  return useQuery({
    queryKey: PERFORMANCE_KEY,
    queryFn: ({ signal }): Promise<Performance> => signalsData('performance', undefined, signal),
    enabled,
    retry,
    staleTime: 5 * 60_000,
  })
}

/** The owner's settings: the language of TRINETRA's explanations, and their version for a save. */
export function useSignalsSettings(enabled: boolean) {
  return useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: ({ signal }): Promise<Settings> => signalsData('settings_get', undefined, signal),
    enabled,
    retry,
    staleTime: 0,
  })
}

/**
 * Save the language (one tap, one request, never retried). On success the settings and the
 * shown state take it at once; on a conflict or an unconfirmed save the settings are read again.
 */
export function useSaveLanguage() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ version, lang }: { version: number; lang: Lang }) =>
      signalsData('settings_set', { version, lang }),
    retry: false,
    onSuccess: (settings: Settings) => {
      client.setQueryData(SETTINGS_KEY, settings)
      client.setQueryData<SignalsView>(STATE_KEY, (shown) =>
        shown?.kind === 'state'
          ? { ...shown, state: { ...shown.state, lang: settings.lang } }
          : shown
      )
    },
    onError: (error: unknown) => {
      const { code } = problemOf(error, 'write')
      if (code === 'version_conflict' || code === 'not_confirmed') {
        void client.invalidateQueries({ queryKey: SETTINGS_KEY })
      }
    },
  })
}
