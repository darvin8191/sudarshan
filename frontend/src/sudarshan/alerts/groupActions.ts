import type { QueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import {
  type GroupRow,
  type MarketAlertsState,
  marketAlertsApi,
  problemOf,
} from '@/api/marketAlerts'
import { toast } from '@/utils/toast'
import { NO_ANSWER_HOLD_MS } from './actions'
import { dropGroup, putGroup, STATE_KEY } from './stateCache'

/**
 * Pause, Resume and Delete of a list alert from the Alerts page (M8.14), by the same rules as an
 * alert's own (actions.ts): each sent once however it is tapped, the card's buttons held from the
 * tap until the answer, and for a while after an answer that never came while the page reads its
 * alerts again. Delete is asked first by the card. What the service says is shown as it came: a
 * stale version, a refusal at the limits, a list alert that is already gone.
 */
export type GroupAction = 'pause' | 'resume' | 'delete'

export interface GroupHeld {
  action: GroupAction
  state: 'sending' | 'unconfirmed'
}

export const GROUP_NO_ANSWER: Record<GroupAction, string> = {
  pause: 'No answer came for this pause. Your alerts are read again to show where it stands.',
  resume: 'No answer came for this resume. Your alerts are read again to show where it stands.',
  delete:
    'No answer came for this delete: it may have gone through. Your alerts are read again to show whether it did.',
}

let holds: ReadonlyMap<number, GroupHeld> = new Map()
const listeners = new Set<() => void>()

function commit(next: ReadonlyMap<number, GroupHeld>): void {
  holds = next
  for (const listener of [...listeners]) listener()
}

function release(id: number, only?: GroupHeld): void {
  if (!holds.has(id) || (only && holds.get(id) !== only)) return
  const next = new Map(holds)
  next.delete(id)
  commit(next)
}

/** The change held on a list alert, if one is. */
export function useGroupHeld(id: number): GroupHeld | undefined {
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

function update(client: QueryClient, change: (state: MarketAlertsState) => MarketAlertsState) {
  client.setQueryData<MarketAlertsState>(STATE_KEY, (state) => (state ? change(state) : state))
}

/** Send one change for one list alert, unless one is already held on it. */
export async function sendGroupChange(
  client: QueryClient,
  group: GroupRow,
  action: GroupAction
): Promise<void> {
  if (holds.has(group.id)) return
  commit(new Map(holds).set(group.id, { action, state: 'sending' }))
  try {
    if (action === 'delete') {
      await marketAlertsApi.removeGroup(group.id)
      update(client, (state) => dropGroup(state, group.id))
      toast.success('List alert deleted. Its history stays.')
    } else {
      const answer = await marketAlertsApi.changeGroup(group.id, {
        version: group.version,
        action,
      })
      update(client, (state) => putGroup(state, answer.group))
      if (answer.fit) toast.info(answer.fit)
      // The members' own statuses come with the read, not with the answer.
      void client.invalidateQueries({ queryKey: STATE_KEY })
    }
    release(group.id)
  } catch (error) {
    const problem = problemOf(error)
    if (problem.code === 'unanswered') {
      const held: GroupHeld = { action, state: 'unconfirmed' }
      commit(new Map(holds).set(group.id, held))
      setTimeout(() => release(group.id, held), NO_ANSWER_HOLD_MS)
      toast.warning(GROUP_NO_ANSWER[action])
      void client.invalidateQueries({ queryKey: STATE_KEY })
      return
    }
    release(group.id)
    if (problem.code === 'gone') {
      update(client, (state) => dropGroup(state, group.id))
      toast.info(problem.message)
      return
    }
    toast.error(problem.message)
    if (['conflict', 'refused', 'limit'].includes(problem.code)) {
      void client.invalidateQueries({ queryKey: STATE_KEY })
    }
  }
}

/** Tests only: forget every hold. */
export function resetGroupActionsForTests(): void {
  commit(new Map())
}
