import type { QueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { toast } from '@/utils/toast'
import { problemOf, screenerApi } from './api'
import { STATE_KEY } from './keys'
import { dropRow, putRow } from './stateCache'
import { type ScreenerState, type ScreenRow, WORDS } from './types'

/**
 * Pause, Resume and Delete from the Screener page (M9.3), each sent once however it is tapped,
 * by the app's rule for sent actions: the screen's buttons hold from the tap until the answer,
 * and an answer that never came holds them a while longer, `NO_ANSWER_HOLD_MS`, while the page
 * reads its screens again to show where the change stands ("Not confirmed"). Kept app-wide, so
 * closing and opening the sheet again does not offer a second send.
 *
 * What the answer says: the screen's new row takes the old one's place; gone drops the card; a
 * conflict or a refusal shows its sentence and reads the state again; anything else shows its
 * sentence. Every sentence is the server's, as it came.
 */
export type ScreenAction = 'pause' | 'resume' | 'delete'

export interface Held {
  action: ScreenAction
  state: 'sending' | 'unconfirmed'
}

/** How long a change with no answer holds its screen's buttons. */
export const NO_ANSWER_HOLD_MS = 15_000

export const NO_ANSWER: Record<ScreenAction, string> = {
  pause: `${WORDS.not_confirmed} Your screens are read again to show where it stands.`,
  resume: `${WORDS.not_confirmed} Your screens are read again to show where it stands.`,
  delete:
    'Not confirmed: it may have been deleted. Your screens are read again to show whether it was.',
}

/** Screens this app dropped itself (deleted here, or found gone): their pages close in silence. */
const dropped = new Set<number>()

export function noteDropped(id: number): void {
  dropped.add(id)
  if (dropped.size > 200) {
    const oldest = dropped.values().next().value
    if (oldest !== undefined) dropped.delete(oldest)
  }
}

export function droppedHere(id: number): boolean {
  return dropped.has(id)
}

let holds: ReadonlyMap<number, Held> = new Map()
const listeners = new Set<() => void>()

function commit(next: ReadonlyMap<number, Held>): void {
  holds = next
  for (const listener of [...listeners]) listener()
}
const hold = (id: number, held: Held) => commit(new Map(holds).set(id, held))
function release(id: number, only?: Held): void {
  if (!holds.has(id) || (only && holds.get(id) !== only)) return
  const next = new Map(holds)
  next.delete(id)
  commit(next)
}

export function useHeld(id: number): Held | undefined {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    () => holds.get(id),
    () => undefined
  )
}

function update(client: QueryClient, change: (state: ScreenerState) => ScreenerState) {
  client.setQueryData<ScreenerState>(STATE_KEY, (state) => (state ? change(state) : state))
}

/** Send one change for one screen, unless one is already held on it. */
export async function sendChange(
  client: QueryClient,
  row: ScreenRow,
  action: ScreenAction,
  options: { removeAdded?: boolean } = {}
): Promise<boolean> {
  if (holds.has(row.id)) return false
  hold(row.id, { action, state: 'sending' })
  try {
    if (action === 'delete') {
      await screenerApi.remove(row.id, options.removeAdded === true)
      noteDropped(row.id)
      update(client, (state) => dropRow(state, row.id))
      toast.success('Screen deleted. Its history stays.')
    } else {
      const next = await screenerApi.change(row.id, { version: row.version, action })
      update(client, (state) => putRow(state, next))
    }
    release(row.id)
    return true
  } catch (error) {
    const problem = problemOf(error)
    if (problem.code === 'unanswered') {
      const held: Held = { action, state: 'unconfirmed' }
      hold(row.id, held)
      setTimeout(() => release(row.id, held), NO_ANSWER_HOLD_MS)
      toast.warning(NO_ANSWER[action])
      void client.invalidateQueries({ queryKey: STATE_KEY })
      return false
    }
    release(row.id)
    if (problem.code === 'gone') {
      noteDropped(row.id)
      update(client, (state) => dropRow(state, row.id))
      toast.info(problem.message)
      return false
    }
    toast.error(problem.code === 'conflict' ? WORDS.conflict : problem.message)
    if (problem.code === 'conflict' || problem.code === 'refused') {
      void client.invalidateQueries({ queryKey: STATE_KEY })
    }
    return false
  }
}

/** Tests only: forget every hold, and every screen dropped. */
export function resetScreenActionsForTests(): void {
  dropped.clear()
  commit(new Map())
}
