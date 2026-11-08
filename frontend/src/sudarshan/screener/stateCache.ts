/**
 * The Screener page's cached `/state`, changed by what the server pushes and by the owner's own
 * changes, as plain functions a test can call. Each returns a new state, or null when the push
 * says something the cache cannot place (a screen it does not hold, a version newer than its
 * own): the page then reads the state again rather than guess.
 */
import type { RunPush, ScreenerState, ScreenRow, StatePush } from './types'

export function putRow(state: ScreenerState, row: ScreenRow): ScreenerState {
  const held = state.screens.some((each) => each.id === row.id)
  const screens = held
    ? state.screens.map((each) => (each.id === row.id ? row : each))
    : [...state.screens, row]
  return { ...state, screens }
}

export function dropRow(state: ScreenerState, id: number): ScreenerState {
  return { ...state, screens: state.screens.filter((each) => each.id !== id) }
}

/** A state push: the engine line, the screens that changed, the screens that went. */
export function applyState(state: ScreenerState, push: StatePush): ScreenerState | null {
  if (push.resync) return null
  const held = new Map(state.screens.map((row) => [row.id, row]))
  for (const part of push.screens) {
    const row = held.get(part.id)
    // A screen this page does not hold, or an edit it has not read: its words may have changed.
    if (!row || part.version > row.version) return null
    if (part.version < row.version) continue
    held.set(part.id, { ...row, ...part })
  }
  for (const id of push.removed) held.delete(id)
  return {
    ...state,
    engine: push.engine,
    screens: state.screens.flatMap((row) => {
      const next = held.get(row.id)
      return next ? [next] : []
    }),
  }
}

/** A run push moves the card's counts; a screen the page does not hold is read again. */
export function applyRun(state: ScreenerState, push: RunPush): ScreenerState | null {
  const row = state.screens.find((each) => each.id === push.screen_id)
  if (!row) return null
  if (push.run_id <= row.last_run_id) return state
  const next: ScreenRow = {
    ...row,
    matches: push.counts.matches,
    entered_today: row.entered_today + push.counts.entered,
    last_run_id: push.run_id,
  }
  return {
    ...putRow(state, next),
    last_run_id: Math.max(state.last_run_id, push.run_id),
  }
}
