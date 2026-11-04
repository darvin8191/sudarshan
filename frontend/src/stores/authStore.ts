import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { useBrokerStore } from './brokerStore'

interface User {
  username: string
  broker: string | null
  isLoggedIn: boolean
  loginTime: string | null
}

interface AuthStore {
  user: User | null
  apiKey: string | null
  isAuthenticated: boolean
  // SUDARSHAN: what /auth/session-status last said about the broker login.
  // Both are this visit's facts, so neither is persisted (see partialize).
  /** The server has answered a session check since the page loaded or the last logout. */
  sessionChecked: boolean
  /** The server said the app session is fine but today's broker login is not (token rolled over). */
  brokerSessionExpired: boolean
  /**
   * SUDARSHAN: counts logouts. A session check that started before a logout
   * compares it on return and drops its answer. Not persisted.
   */
  logoutEpoch: number

  setUser: (user: User) => void
  setApiKey: (apiKey: string | null) => void
  setBrokerSession: (expired: boolean) => void
  login: (username: string, broker: string) => void
  logout: () => void
  checkSession: () => boolean
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set, get) => ({
      user: null,
      apiKey: null,
      isAuthenticated: false,
      sessionChecked: false,
      brokerSessionExpired: false,
      logoutEpoch: 0,

      setUser: (user) => set({ user, isAuthenticated: user.isLoggedIn }),

      setApiKey: (apiKey) => set({ apiKey }),

      setBrokerSession: (expired) => set({ sessionChecked: true, brokerSessionExpired: expired }),

      login: (username, broker) => {
        const user: User = {
          username,
          broker,
          isLoggedIn: true,
          loginTime: new Date().toISOString(),
        }
        set({ user, isAuthenticated: true })
      },

      logout: () => {
        set((state) => ({
          logoutEpoch: state.logoutEpoch + 1,
          user: null,
          isAuthenticated: false,
          apiKey: null,
          sessionChecked: false,
          brokerSessionExpired: false,
        }))
      },

      checkSession: () => {
        const { user } = get()
        if (!user || !user.loginTime) return false

        // Skip session expiry for crypto brokers (24/7 markets)
        const capabilities = useBrokerStore.getState().capabilities
        if (capabilities?.broker_type === 'crypto') {
          return true
        }

        // Session expiry check (3 AM IST daily)
        const now = new Date()
        const loginTime = new Date(user.loginTime)

        // Convert to IST properly: UTC + 5.5 hours
        // First get UTC time, then add IST offset
        const istOffsetMs = 5.5 * 60 * 60 * 1000
        const localOffsetMs = now.getTimezoneOffset() * 60 * 1000

        // Convert current time to IST
        const nowUTC = now.getTime() + localOffsetMs
        const nowIST = new Date(nowUTC + istOffsetMs)

        // Convert login time to IST
        const loginUTC = loginTime.getTime() + localOffsetMs
        const loginIST = new Date(loginUTC + istOffsetMs)

        // Create today's 3 AM IST expiry time
        const todayExpiry = new Date(nowIST)
        todayExpiry.setHours(3, 0, 0, 0)

        // If current time is after 3 AM IST today and login was before 3 AM IST today
        if (nowIST > todayExpiry && loginIST < todayExpiry) {
          get().logout()
          return false
        }

        return true
      },
    }),
    {
      name: 'openalgo-auth',
      // SUDARSHAN: the same three fields as before; the broker-session facts
      // must come from this visit's own check, never from an old one.
      partialize: ({ user, apiKey, isAuthenticated }) => ({ user, apiKey, isAuthenticated }),
    }
  )
)
