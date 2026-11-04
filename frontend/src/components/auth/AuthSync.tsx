import { useEffect, useState } from 'react'
import { useAuthStore } from '@/stores/authStore'
import { useBrokerStore } from '@/stores/brokerStore'
import { useSessionStore } from '@/stores/sessionStore'
import { useThemeStore } from '@/stores/themeStore'

interface AuthSyncProps {
  children: React.ReactNode
}

export interface SyncOptions {
  /**
   * Leave the stores as they are when the server cannot be read (no answer,
   * or one that is not OK), instead of logging out as the check on page load
   * does. For a check right after a login, and for the screens' re-checks: a
   * server restarting for a deploy must not log anyone out.
   */
  keepOnFailure?: boolean
}

/** The last check sent, and the last whose answer reached the stores. */
let sent = 0
let applied = 0

/**
 * SUDARSHAN: the session check AuthSync makes on page load, as one function
 * every caller shares: AuthSync, the login page after a login inside the app,
 * and the screens waiting for a broker login (src/sudarshan/access.ts). It
 * brings the user, the API key, today's broker login, the broker's
 * capabilities, the app mode and the session count from the server into the
 * stores. Upstream ran it on page load only, so a login inside the app left
 * the API key unset until a reload. An answer that a later check's answer has
 * already overtaken is dropped. Never throws.
 */
export async function syncSession({ keepOnFailure = false }: SyncOptions = {}): Promise<void> {
  const ticket = ++sent
  // A logout while this check is in flight outdates its answer.
  const startedAt = useAuthStore.getState().logoutEpoch
  const signedOutSince = () => useAuthStore.getState().logoutEpoch !== startedAt
  try {
    const response = await fetch('/auth/session-status', {
      credentials: 'include',
    })
    const data = response.ok ? await response.json() : null
    if (ticket < applied || (!response.ok && keepOnFailure) || signedOutSince()) return
    applied = ticket
    const { setUser, setApiKey, setBrokerSession, logout } = useAuthStore.getState()
    const { fetchCapabilities, clearCapabilities } = useBrokerStore.getState()

    if (data?.status === 'success' && data.logged_in && data.broker) {
      // Flask session is authenticated with broker - sync to Zustand
      setUser({
        username: data.user,
        broker: data.broker,
        isLoggedIn: true,
        loginTime: new Date().toISOString(),
      })
      // Store the API key for trading API calls
      // An answer with no key clears the saved one too: it is gone on the server.
      setApiKey(data.api_key ? data.api_key : null)
      // SUDARSHAN: whether today's broker login is still good. After the key,
      // so a screen never sees "logged in, no key" on its way to ready.
      setBrokerSession(data.broker_session_expired === true)
      // Fetch broker capabilities (exchanges, type, features)
      await fetchCapabilities()
      if (signedOutSince()) {
        clearCapabilities()
        return
      }
      // Also sync app mode from backend
      await useThemeStore.getState().syncAppMode()
      // Sync active session count
      if (data.active_sessions !== undefined) {
        useSessionStore.getState().setActiveSessionCount(data.active_sessions)
      }
    } else if (data?.status === 'success' && data.authenticated && !data.logged_in) {
      // User is logged in but hasn't connected broker yet
      setUser({
        username: data.user,
        broker: null,
        isLoggedIn: false,
        loginTime: null,
      })
      setBrokerSession(false)
      clearCapabilities()
    } else {
      // Not authenticated, status is not success, or any non-OK response
      // (401, 500, etc.) - clear Zustand store
      logout()
      clearCapabilities()
    }
  } catch (_error) {
    // On error, don't change auth state - let existing state persist
  }
}

/**
 * AuthSync component that synchronizes Flask session with Zustand store.
 * This ensures the React app knows about authentication state from OAuth callbacks.
 * Also syncs app mode (live/analyzer) from the backend.
 */
export function AuthSync({ children }: AuthSyncProps) {
  const [isChecking, setIsChecking] = useState(true)

  useEffect(() => {
    void syncSession().finally(() => setIsChecking(false))
  }, [])

  // Show nothing while checking - prevents flash of wrong content
  if (isChecking) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    )
  }

  return <>{children}</>
}
