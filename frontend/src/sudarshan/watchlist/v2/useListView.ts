import { type QueryClient, type QueryKey, useQuery, useQueryClient } from '@tanstack/react-query'
import { callOp, type WatchlistsError } from './api'
import type { Lists, Push, State, View } from './types'

/**
 * The v2 reads (state, the All lists answer, one list's view) as TanStack Query entries, and what
 * a change push does to them. Nothing here reads on a timer: an entry is read when a screen first
 * asks, and again only when a push, a reconnect or a write says it changed (usePush.ts).
 *
 * While the switch is off, `state` is the only read the page makes (`enabled: false`), and the
 * rest wait for it to say the features are on.
 */
export const V2_KEY = ['sudarshan', 'watchlists', 'v2'] as const
export const STATE_KEY = [...V2_KEY, 'state'] as const
export const LISTS_KEY = [...V2_KEY, 'lists'] as const
export const viewKey = (ref: string) => [...V2_KEY, 'view', ref] as const

/**
 * Which of two list versions (`"<boot>:<n>"`) is newer: 1, -1, 0 when equal, and null when they
 * cannot be told apart (another boot: the server restarted and its counter began again).
 */
export function compareVersions(a: string, b: string): -1 | 0 | 1 | null {
  if (a === b) return 0
  const ia = a.lastIndexOf(':')
  const ib = b.lastIndexOf(':')
  if (ia < 0 || ib < 0 || a.slice(0, ia) !== b.slice(0, ib)) return null
  const na = Number(a.slice(ia + 1))
  const nb = Number(b.slice(ib + 1))
  if (!Number.isInteger(na) || !Number.isInteger(nb)) return null
  return na > nb ? 1 : -1
}

/**
 * The version rule: an older answer never replaces a newer one. A read that began before a change
 * can finish after the read that saw it; whichever is older is dropped for what is held. Answers
 * from different boots cannot be ordered, and the one that arrives last (a restart in between) wins.
 */
export function keepNewer<T extends { version: string }>(held: T | undefined, got: T): T {
  return held && compareVersions(got.version, held.version) === -1 ? held : got
}

/** The state read: whether v2 is on, its limits, the flag names, the favourites. */
export function useV2State() {
  return useQuery<State, WatchlistsError>({
    queryKey: STATE_KEY,
    queryFn: ({ signal }) => callOp('state', {}, signal),
    staleTime: 30_000,
    retry: false,
  })
}

/** True only once the state has said the features are on. An unanswered state is off. */
export function useV2On(): boolean {
  return useV2State().data?.enabled === true
}

/** One list merged (the `view` op), read only while v2 is on and a list is open. */
export function useListView(ref: string | null) {
  const client = useQueryClient()
  const on = useV2On()
  return useQuery<View, WatchlistsError>({
    queryKey: viewKey(ref ?? ''),
    queryFn: async ({ signal }) => {
      const got = await callOp('view', { ref: ref ?? '' }, signal)
      // What is held is looked at when the answer arrives, not when the read began.
      return keepNewer(client.getQueryData<View>(viewKey(ref ?? '')), got)
    },
    enabled: on && ref !== null,
    staleTime: 30_000,
    retry: false,
  })
}

/** Every list for the All lists sheet (the `lists` op), read when the sheet opens. */
export function useLists(wanted = true) {
  const client = useQueryClient()
  const on = useV2On()
  return useQuery<Lists, WatchlistsError>({
    queryKey: LISTS_KEY,
    queryFn: async ({ signal }) => {
      const got = await callOp('lists', {}, signal)
      return keepNewer(client.getQueryData<Lists>(LISTS_KEY), got)
    },
    enabled: on && wanted,
    staleTime: 30_000,
    retry: false,
  })
}

/** What a push changed, as the reads to run again. */
export function keysFor(push: Push): QueryKey[] {
  switch (push.why) {
    case 'flags':
      // Flag names, the flag counts, and the markers every open view carries.
      return [STATE_KEY, LISTS_KEY, [...V2_KEY, 'view'], [...V2_KEY, 'symbol']]
    case 'favourites':
      return [STATE_KEY, LISTS_KEY]
    case 'lists':
      return [LISTS_KEY, viewKey(push.ref)]
    case 'items':
      return [viewKey(push.ref), LISTS_KEY]
    default:
      // sections, arranged, rows, prefs
      return [viewKey(push.ref)]
  }
}

/**
 * Read again what these pushes changed, once each however many pushes named it. A push whose
 * version is the one the named view already holds, or an older one, changes nothing and reads
 * nothing (the version rule): this device made the change and already has it.
 */
export function applyPushes(client: QueryClient, pushes: Push[]): void {
  const keys = new Map<string, QueryKey>()
  for (const push of pushes) {
    if (push.ref) {
      const held = client.getQueryData<View>(viewKey(push.ref))
      const order = held ? compareVersions(push.v, held.version) : null
      if (order !== null && order <= 0 && push.why !== 'flags') continue
    }
    for (const key of keysFor(push)) keys.set(JSON.stringify(key), key)
  }
  for (const key of keys.values()) void client.invalidateQueries({ queryKey: key })
}
