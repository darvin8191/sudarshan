/**
 * SUDARSHAN: what a sign-out does to what the browser keeps (release review v0.8.0).
 *
 * M2: the app keeps the API key in the browser's storage (stores/authStore.ts persists it), and a
 * logout or a password change does not change the key, which places orders from the server's own
 * address. Every place that learns the session has ended (a read answered 401, a live connection
 * refused for a session that ended, a logout) forgets the key at once, before it goes to the login
 * page. Before, the copy stayed until the next page load's session check cleared it, and that
 * check clears it only when it reaches the server.
 *
 * L6: when the server could not record the logout time, the other devices stay signed in until
 * 03:00, and the logout's answer says so; the owner's logout shows that sentence instead of
 * "Logged out successfully".
 */
import { useAuthStore } from '@/stores/authStore'

/** Forget the API key kept in this browser, in the store and in its storage. */
export function forgetApiKey(): void {
  useAuthStore.getState().setApiKey(null)
}

export interface LogoutAnswer {
  status?: string
  message?: string
}

/** The toast after the owner's own logout: the server's sentence when it could not sign the
 * other devices out, otherwise the usual one. */
export function logoutWords(answer: LogoutAnswer | undefined): {
  kind: 'success' | 'error'
  text: string
} {
  if (answer && answer.status === 'error' && answer.message) {
    return { kind: 'error', text: answer.message }
  }
  return { kind: 'success', text: 'Logged out successfully' }
}
