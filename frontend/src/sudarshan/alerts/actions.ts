import type { QueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import {
  type AlertRow,
  type MarketAlertsState,
  marketAlertsApi,
  problemOf,
} from '@/api/marketAlerts'
import { toast } from '@/utils/toast'
import { dropRow, putRow, STATE_KEY } from './stateCache'

/**
 * Pause, Resume, Restart and Delete from the Alerts page (M10.3), each sent once however it is
 * tapped, by the app's rule for sent actions: the alert's buttons hold from the tap until the
 * answer, and an answer that never came holds them a while longer, `NO_ANSWER_HOLD_MS`, while the
 * page reads its alerts again to show where the change stands. Kept app-wide, so closing and
 * opening the sheet again does not offer a second send.
 *
 * What the answer says (contract 1): the alert's new row takes the old one's place; gone drops the
 * card; a conflict or a refusal shows its sentence and reads the state again; anything else shows
 * its sentence. Every sentence is the server's, as it came.
 */
export type AlertAction = 'pause' | 'resume' | 'restart' | 'delete'

export interface Held {
  action: AlertAction
  state: 'sending' | 'unconfirmed'
}

/** How long a change with no answer holds its alert's buttons. */
export const NO_ANSWER_HOLD_MS = 15_000

/** What the page says when no answer came: the change may or may not have been made. */
export const NO_ANSWER: Record<AlertAction, string> = {
  pause: 'No answer came for this pause. Your alerts are read again to show where it stands.',
  resume: 'No answer came for this resume. Your alerts are read again to show where it stands.',
  restart: 'No answer came for this restart. Your alerts are read again to show where it stands.',
  delete:
    'No answer came for this delete: it may have gone through. Your alerts are read again to show whether it did.',
}

let holds: ReadonlyMap<number, Held> = new Map()
const listeners = new Set<() => void>()

function commit(next: ReadonlyMap<number, Held>): void {
  holds = next
  for (const listener of [...listeners]) listener()
}

function hold(id: number, held: Held): void {
  commit(new Map(holds).set(id, held))
}

function release(id: number, only?: Held): void {
  if (!holds.has(id) || (only && holds.get(id) !== only)) return
  const next = new Map(holds)
  next.delete(id)
  commit(next)
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** The change held on an alert, if one is. */
export function useHeld(id: number): Held | undefined {
  return useSyncExternalStore(
    subscribe,
    () => holds.get(id),
    () => undefined
  )
}

function update(client: QueryClient, change: (state: MarketAlertsState) => MarketAlertsState) {
  client.setQueryData<MarketAlertsState>(STATE_KEY, (state) => (state ? change(state) : state))
}

/** Send one change for one alert, unless one is already held on it. */
export async function sendChange(
  client: QueryClient,
  row: AlertRow,
  action: AlertAction
): Promise<void> {
  if (holds.has(row.id)) return
  hold(row.id, { action, state: 'sending' })
  try {
    if (action === 'delete') {
      await marketAlertsApi.remove(row.id)
      update(client, (state) => dropRow(state, row.id))
      toast.success('Alert deleted. Its history stays.')
    } else {
      const next = await marketAlertsApi.change(row.id, { version: row.version, action })
      update(client, (state) => putRow(state, next))
    }
    release(row.id)
  } catch (error) {
    const problem = problemOf(error)
    if (problem.code === 'unanswered') {
      const held: Held = { action, state: 'unconfirmed' }
      hold(row.id, held)
      setTimeout(() => release(row.id, held), NO_ANSWER_HOLD_MS)
      toast.warning(NO_ANSWER[action])
      void client.invalidateQueries({ queryKey: STATE_KEY })
      return
    }
    release(row.id)
    if (problem.code === 'gone') {
      update(client, (state) => dropRow(state, row.id))
      toast.info(problem.message)
      return
    }
    toast.error(problem.message)
    if (problem.code === 'conflict' || problem.code === 'refused') {
      void client.invalidateQueries({ queryKey: STATE_KEY })
    }
  }
}

/** Tests only: forget every hold. */
export function resetAlertActionsForTests(): void {
  commit(new Map())
}
