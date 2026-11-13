import { type QueryClient, type QueryKey, useQuery, useQueryClient } from '@tanstack/react-query'
import { WATCHLISTS_KEY } from '../useWatchlists'
import { callOp, type WatchlistsError } from './api'
import { takeOwn } from './ownWrites'
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
 * Two versions, and each is used where it belongs.
 *
 * The lists version (`state.versions.lists`, `lists.version`, every push's `v`) is `"<boot>:<n>"`:
 * a word that says "the lists as of this moment", compared for equality only, never ordered. It
 * decides whether a push says something the page does not already know.
 *
 * A list's own version (`view.version`) is a plain number as text ("0" until the list is written,
 * then the epoch milliseconds of a write, one more for each). It is only the version a write is
 * decided on, and the one rule below that keeps a slow read from replacing a newer one.
 */
const WHOLE = /^\d+$/

/**
 * The view rule: an older answer never replaces a newer one. A read that began before a change
 * can finish after the read that saw it; the older is dropped for what is held. Both versions
 * must be whole numbers to be ordered, and "0" (a list with no overlay row: new, or its row was
 * pruned and the id reused) is never taken for older, because it can stand for a different list.
 */
export function keepNewer<T extends { version: string }>(held: T | undefined, got: T): T {
  if (!held || !WHOLE.test(held.version) || !WHOLE.test(got.version) || got.version === '0') {
    return got
  }
  return Number(got.version) < Number(held.version) ? held : got
}

/** The lists version this page last saw, per client: from a `state` or `lists` answer or a push. */
const seenLists = new WeakMap<QueryClient, string>()

export function rememberListsVersion(client: QueryClient, version: string | null | undefined) {
  if (typeof version === 'string' && version) seenLists.set(client, version)
}

/** The state read: whether v2 is on, its limits, the flag names, the favourites. */
export function useV2State() {
  const client = useQueryClient()
  return useQuery<State, WatchlistsError>({
    queryKey: STATE_KEY,
    queryFn: async ({ signal }) => {
      const got = await callOp('state', {}, signal)
      rememberListsVersion(client, got.versions?.lists)
      return got
    },
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
      rememberListsVersion(client, got.version)
      return got
    },
    enabled: on && wanted,
    staleTime: 30_000,
    retry: false,
  })
}

/**
 * Every ref a push names: `refs` when it carries them, else the one `ref` (maybe "lists") for an
 * older server that sends no `refs`. An empty `refs` is lane W's two cases: a change that touched
 * no list's members (favourites, a flag rename, a note), or one whose list the hook could not
 * tell. `keysFor` reads every open view for the second, so it is not decided here.
 */
export function refsOf(push: Push): string[] {
  if (Array.isArray(push.refs)) return push.refs.filter((r) => typeof r === 'string')
  return [push.ref]
}

/** One read to run again; `exact` keeps a key that is a prefix of others to itself. */
interface Reread {
  key: QueryKey
  exact: boolean
}

const OWN = (ref: string) => ref === 'lists' || ref.startsWith('list:')

/**
 * What a push changed, as the reads to run again.
 *
 * The rows and the tabs come from OpenAlgo's lists (`WATCHLISTS_KEY`), not from `view`, so a push
 * that changes what a list holds, or which lists there are, reads them again beside the open
 * views: a symbol added on /trading shows on the phone. A push naming several lists (a move)
 * reads each named list's view; "lists" with no names reads every open one.
 */
export function keysFor(push: Push): Reread[] {
  const named = refsOf(push)
  // An empty refs from another screen reads every open view, except a favourites or prefs push.
  const refs =
    named.length === 0 && push.why !== 'favourites' && push.why !== 'prefs' ? ['lists'] : named
  const views = (): Reread[] =>
    refs.map((ref) =>
      ref === 'lists'
        ? { key: [...V2_KEY, 'view'], exact: false }
        : { key: viewKey(ref), exact: true }
    )
  const lists: Reread = { key: WATCHLISTS_KEY, exact: true }
  const own = refs.some(OWN)
  switch (push.why) {
    case 'flags':
      // Flag names, the flag counts, and the markers every open view carries.
      return [
        { key: STATE_KEY, exact: true },
        { key: LISTS_KEY, exact: true },
        { key: [...V2_KEY, 'view'], exact: false },
        { key: [...V2_KEY, 'symbol'], exact: false },
      ]
    case 'favourites':
      return [
        { key: STATE_KEY, exact: true },
        { key: LISTS_KEY, exact: true },
      ]
    case 'lists':
      return [{ key: LISTS_KEY, exact: true }, lists, ...views()]
    case 'items':
      return [...views(), { key: LISTS_KEY, exact: true }, ...(own ? [lists] : [])]
    case 'arranged':
      return [...views(), ...(own ? [lists] : [])]
    default:
      // sections, rows, prefs: the overlay only
      return views()
  }
}

/**
 * Read again what these pushes changed, once each however many pushes named it. A push whose
 * lists version is the one this page last saw, or the one a write of this page answered with
 * (`ownWrites.ts`), says nothing new and reads nothing (equality only;
 * the flag and favourite versions have their own and are never skipped). While a read is in
 * flight the page does not know whether that read saw the change, so nothing is skipped then.
 */
export function applyPushes(client: QueryClient, pushes: Push[]): void {
  const wanted = new Map<string, Reread>()
  const quiet = client.isFetching({ queryKey: V2_KEY }) === 0
  for (const push of pushes) {
    const separate = push.why === 'flags' || push.why === 'favourites'
    // The push of a write this page made: its answer already re-read what it changed.
    const own = !separate && takeOwn(push.v)
    const known = quiet && push.v === seenLists.get(client)
    if (!own && (!known || separate)) {
      for (const read of keysFor(push)) wanted.set(JSON.stringify([read.key, read.exact]), read)
    }
    rememberListsVersion(client, push.v)
  }
  for (const { key, exact } of wanted.values()) {
    void client.invalidateQueries({ queryKey: key, exact })
  }
}
