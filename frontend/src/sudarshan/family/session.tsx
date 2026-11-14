// SUDARSHAN P6 D2: the viewer's session, read once by FamilyLayout and shared with its screens.
//
// Review M5: everything the page holds of a member (the books, the summary, the shared lists and
// tabs, the devices and settings, the session itself) is a query under `['family']`, and none of
// it may be shown to the next member who signs in on the same phone. So it is forgotten on every
// way a session ends (a sign-out here, one the server made, one made in another tab) and before a
// new one is stored, and the shell renders no screen while the cache was filled for another member.
import type { QueryClient } from '@tanstack/react-query'
import { createContext, useContext } from 'react'
import type { Me } from './types'

export const ME_KEY = ['family', 'me'] as const
export const FAMILY_KEY = ['family'] as const

export const FamilyMeContext = createContext<Me | null>(null)

/** The signed-in member. Only valid below FamilyLayout, which renders nothing until it is known. */
export function useFamilyMe(): Me {
  const me = useContext(FamilyMeContext)
  if (!me) throw new Error('useFamilyMe is used below FamilyLayout only')
  return me
}

/** The member, or null outside the layout (a test of one screen with no shell). */
export function useFamilyMeOptional(): Me | null {
  return useContext(FamilyMeContext)
}

/** Which member each query client's family queries were filled for. */
const filledFor = new WeakMap<QueryClient, number>()

/**
 * Forget every family query, cancelled first so that an answer still on its way for the member
 * before cannot land after. `keepSession` keeps the session's own query, for the shell that is
 * still on screen and reads it (removing it there would only make it ask again at once).
 */
export function forgetMember(client: QueryClient, keepSession = false): void {
  const mine = keepSession
    ? (query: { queryKey: readonly unknown[] }) => query.queryKey[1] !== ME_KEY[1]
    : () => true
  void client.cancelQueries({ queryKey: FAMILY_KEY, predicate: mine })
  client.removeQueries({ queryKey: FAMILY_KEY, predicate: mine })
  filledFor.delete(client)
}

/** Whether the family queries hold this member's figures and nobody else's. */
export function filledForMember(client: QueryClient, memberId: number): boolean {
  return filledFor.get(client) === memberId
}

/** The family queries are this member's from now: whatever another member left is forgotten. */
export function claimForMember(client: QueryClient, memberId: number): void {
  if (filledFor.get(client) !== memberId) forgetMember(client, true)
  filledFor.set(client, memberId)
}

// ---- why the sign-in page is showing (review M1, v0.12.0) ------------------------------------
// A session the server ended (the owner paused or removed the member, it expired, another device
// signed this one out) answers `me` with a sentence, and the member lands on Sign in. The sentence
// goes with them, in the router's state. A phone that was never signed in, or whose member signed
// out here themselves, gets the same refusal with no cause to tell: this flag, kept on the phone,
// is how the shell knows a session was open here. It holds no figure and names nobody.
const WAS_IN = 'sd-family-was-in'

/** What the sign-in page is handed when the shell sends a member there. */
export interface LoginState {
  reason: string
}

/** A session is open on this phone. */
export function markSignedIn(): void {
  try {
    window.localStorage.setItem(WAS_IN, '1')
  } catch {
    // not kept: see `wasSignedIn`
  }
}

/** The member left by their own tap, or the sign-in page has said why: nothing more to tell. */
export function clearSignedIn(): void {
  try {
    window.localStorage.removeItem(WAS_IN)
  } catch {
    // nothing was kept
  }
}

/** Whether a session was open here. Where the phone keeps nothing, the sentence is shown. */
export function wasSignedIn(): boolean {
  try {
    return window.localStorage.getItem(WAS_IN) === '1'
  } catch {
    return true
  }
}

/** The sentence the sign-in page was sent, or null (opened directly, or the state is not ours). */
export function reasonOf(state: unknown): string | null {
  if (!state || typeof state !== 'object') return null
  const reason = (state as { reason?: unknown }).reason
  return typeof reason === 'string' && reason.trim() ? reason.trim() : null
}
