import type { InfiniteData, QueryClient } from '@tanstack/react-query'
import { useEffect, useSyncExternalStore } from 'react'
import type { Socket } from 'socket.io-client'
import { connectionCount } from '@/sudarshan/books/sharedSocket'
import { WATCHLISTS_KEY } from '@/sudarshan/watchlist/useWatchlists'
import { screenerApi } from './api'
import { MEMBERSHIP_KEY, RESULTS_KEY, RUNS_KEY, runsKey, STATE_KEY, TODAY_KEY } from './keys'
import { applyRun, applyState } from './stateCache'
import type { Results, RunPush, RunRow, RunsPage, ScreenerState, StatePush } from './types'

/**
 * Joining the owner's room and applying what the screener pushes (design 4.5), for the Screener
 * page only: it listens while the page is open and stops when it closes.
 *
 * After every connect, the first and each reconnect, it joins with an ack (a session that is not
 * the owner's is not joined, and hears nothing), then asks for the runs after the last one it
 * saw, so what ran while the phone was away is on screen at once. `hearing` says whether this
 * screen is in the room: until it is, the page does not claim to be real-time.
 */
export type Hearing = 'unknown' | 'joined' | 'not_joined'

/** How long a join waits for its ack; without one, the next connect tries again. */
export const JOIN_WAIT_MS = 10_000
/** How long a join without an ack waits before it is tried again. */
export const RETRY_MS = [2_000, 5_000, 10_000] as const
/** Pages of "runs since" read at most in one catch-up. */
const SINCE_PAGES = 5

let hearing: Hearing = 'unknown'
const listeners = new Set<() => void>()

function hear(next: Hearing): void {
  if (hearing === next) return
  hearing = next
  for (const listener of [...listeners]) listener()
}

export function useHearing(): Hearing {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    () => hearing,
    () => 'unknown'
  )
}

/** The newest run id this page has seen: the catch-up asks for what came after it. */
let seen = 0

export function noteSeen(runId: number): void {
  if (runId > seen) seen = runId
}

/**
 * A pushed run patches what the page holds: the screen's card, its open results and its history
 * (a new row at the top). Runs the cache cannot place are read again.
 */
export function onRun(client: QueryClient, push: RunPush): void {
  noteSeen(push.run_id)
  const state = client.getQueryData<ScreenerState>(STATE_KEY)
  const next = state ? applyRun(state, push) : null
  if (state && next) client.setQueryData<ScreenerState>(STATE_KEY, next)
  else void client.invalidateQueries({ queryKey: STATE_KEY })
  client.setQueryData<Results>([...RESULTS_KEY, push.screen_id], (held) => {
    if (!held) return held
    return mergeResults(held, push)
  })
  // The patch is on screen; the read that follows brings the chips and the clause values of the
  // rows that stayed, which a push this small cannot carry.
  void client.invalidateQueries({ queryKey: [...RESULTS_KEY, push.screen_id] })
  client.setQueryData<InfiniteData<RunsPage, number | null>>(runsKey(push.screen_id), (held) =>
    held ? putRun(held, runOf(push)) : held
  )
  void client.invalidateQueries({ queryKey: [...RUNS_KEY, push.screen_id] })
  void client.invalidateQueries({ queryKey: TODAY_KEY })
  void client.invalidateQueries({ queryKey: MEMBERSHIP_KEY })
}

/** The history row a push stands for. */
function runOf(push: RunPush): RunRow {
  return {
    id: push.run_id,
    screen_id: push.screen_id,
    at: push.at,
    candle: push.candle,
    matches: push.counts.matches,
    entered: push.counts.entered,
    exited: push.counts.exited,
    marks: push.marks,
  }
}

/** A run joins the top of the history held, once; one already held changes nothing. */
function putRun(held: InfiniteData<RunsPage, number | null>, run: RunRow) {
  if (held.pages.some((page) => page.runs.some((have) => have.id === run.id))) return held
  const [first, ...rest] = held.pages
  if (!first) return held
  return { ...held, pages: [{ ...first, runs: [run, ...first.runs] }, ...rest] }
}

/**
 * The results after a run: the run's entered rows join, its exited rows leave, the counts move.
 * The membership chips and the clause values of the rows that stayed come from the next read of
 * the results, which a push this small cannot carry; the page reads them once the push is in.
 */
export function mergeResults(held: Results, push: RunPush): Results {
  // A run the page already shows, or an older one that arrived late, changes nothing.
  if (held.run && held.run.id >= push.run_id) return held
  const gone = new Set(push.exited.map((row) => `${row.exchange}:${row.symbol}`))
  const keep = held.rows.filter((row) => !gone.has(`${row.exchange}:${row.symbol}`))
  const have = new Set(keep.map((row) => `${row.exchange}:${row.symbol}`))
  const added = push.entered.filter((row) => !have.has(`${row.exchange}:${row.symbol}`))
  const run = runOf(push)
  return {
    ...held,
    run,
    rows: [...added, ...keep],
    not_checked: { ...held.not_checked, count: push.counts.not_checked },
  }
}

export function onStatePush(client: QueryClient, push: StatePush): void {
  const state = client.getQueryData<ScreenerState>(STATE_KEY)
  const next = state ? applyState(state, push) : null
  if (state && next) client.setQueryData<ScreenerState>(STATE_KEY, next)
  else void client.invalidateQueries({ queryKey: STATE_KEY })
}

/** Read what ran after the last run seen, oldest first, and apply each as a pushed run would. */
export async function catchUp(client: QueryClient): Promise<void> {
  if (seen === 0) {
    const state = await screenerApi.state()
    client.setQueryData(STATE_KEY, state)
    noteSeen(state.last_run_id)
    return
  }
  let after = seen
  let moved = false
  for (let page = 0; page < SINCE_PAGES; page += 1) {
    const { runs, more } = await screenerApi.runsSince(after)
    for (const run of runs) {
      if (run.id > after) after = run.id
      moved = true
    }
    if (!more || runs.length === 0) break
  }
  noteSeen(after)
  if (moved) {
    // Which screens moved, and their lists, is the state's to say.
    void client.invalidateQueries({ queryKey: STATE_KEY })
    void client.invalidateQueries({ queryKey: RESULTS_KEY })
    void client.invalidateQueries({ queryKey: RUNS_KEY })
    void client.invalidateQueries({ queryKey: TODAY_KEY })
  }
}

/** Listen on the app's shared connection while the page is open. */
/**
 * A screener changed a list (an add, an exit, a pin): the chips, the watchlists themselves and the
 * screens' counts are read again. The push says only that, so nothing is patched from it.
 */
export function onListsPush(client: QueryClient): void {
  void client.invalidateQueries({ queryKey: MEMBERSHIP_KEY })
  void client.invalidateQueries({ queryKey: WATCHLISTS_KEY })
  void client.invalidateQueries({ queryKey: STATE_KEY })
}

export function useScreenerPush(client: QueryClient, socket: Socket | null): void {
  useEffect(() => {
    if (!socket) {
      hear('unknown')
      return
    }
    let alive = true
    const timers = new Set<ReturnType<typeof setTimeout>>()
    let connection = connectionCount(socket) ?? 1

    const later = (attempt: number, again: () => void) => {
      const wait = RETRY_MS[attempt]
      if (wait === undefined) return
      const timer = setTimeout(() => {
        timers.delete(timer)
        if (alive) again()
      }, wait)
      timers.add(timer)
    }
    const current = (at: number) =>
      alive && socket.connected && (connectionCount(socket) ?? 1) === at

    const read = (at: number, attempt = 0) => {
      catchUp(client).catch(() => later(attempt, () => current(at) && read(at, attempt + 1)))
    }
    const join = (at: number, attempt = 0) => {
      socket
        .timeout(JOIN_WAIT_MS)
        .emit('sudarshan_screener_join', (error: unknown, ack?: { ok?: unknown }) => {
          if (!current(at)) return
          if (error || !ack || ack.ok !== true) {
            // No ack, or not the owner's session: not in the room.
            later(attempt, () => current(at) && join(at, attempt + 1))
            return
          }
          hear('joined')
          read(at)
        })
    }
    const onConnect = () => {
      connection = connectionCount(socket) ?? 1
      hear('not_joined')
      join(connection)
    }
    const onDisconnect = () => hear('not_joined')
    const run = (push: RunPush) => onRun(client, push)
    const state = (push: StatePush) => onStatePush(client, push)
    const lists = () => onListsPush(client)

    socket.on('connect', onConnect)
    socket.on('disconnect', onDisconnect)
    socket.on('sudarshan_screener_run', run)
    socket.on('sudarshan_screener_state', state)
    socket.on('sudarshan_screener_lists', lists)
    if (socket.connected) onConnect()
    else hear('not_joined')
    return () => {
      alive = false
      for (const timer of timers) clearTimeout(timer)
      socket.off('connect', onConnect)
      socket.off('disconnect', onDisconnect)
      socket.off('sudarshan_screener_run', run)
      socket.off('sudarshan_screener_state', state)
      socket.off('sudarshan_screener_lists', lists)
      hear('unknown')
    }
  }, [client, socket])
}

/** Tests only: forget what was seen and where the screen stands. */
export function resetScreenerPushForTests(): void {
  seen = 0
  hearing = 'unknown'
}
