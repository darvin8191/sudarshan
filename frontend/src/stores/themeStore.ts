import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * SUDARSHAN is light only. `mode` stays in the store because upstream readers
 * branch on it, but it is always 'light': a persisted 'dark' is read back as
 * light, setMode('dark') and toggleMode() do nothing, and nothing in this store
 * puts the `.dark` class on <html>. The accent is fixed the same way: `color`
 * stays 'zinc', upstream's no-override value, and the brand is set in CSS.
 */
export type ThemeMode = 'light' | 'dark'
export type AppMode = 'live' | 'analyzer'
export type ThemeColor =
  | 'zinc'
  | 'slate'
  | 'stone'
  | 'gray'
  | 'neutral'
  | 'red'
  | 'rose'
  | 'orange'
  | 'green'
  | 'blue'
  | 'yellow'
  | 'violet'

// Event emitter for mode changes
type ModeChangeCallback = (newMode: AppMode) => void
const modeChangeListeners: Set<ModeChangeCallback> = new Set()

export const onModeChange = (callback: ModeChangeCallback): (() => void) => {
  modeChangeListeners.add(callback)
  return () => {
    modeChangeListeners.delete(callback)
  }
}

const notifyModeChange = (newMode: AppMode) => {
  modeChangeListeners.forEach((cb) => cb(newMode))
}

/** The mode classes on <html>: `analyzer` or none. Never `dark`. */
const applyModeClasses = (appMode: AppMode) => {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.classList.remove('analyzer', 'sandbox', 'dark')
  if (appMode === 'analyzer') root.classList.add('analyzer')
}

interface ThemeStore {
  mode: ThemeMode
  color: ThemeColor
  appMode: AppMode
  isTogglingMode: boolean

  setMode: (mode: ThemeMode) => void
  setColor: (color: ThemeColor) => void
  setAppMode: (appMode: AppMode) => void
  toggleMode: () => void
  toggleAppMode: () => Promise<{ success: boolean; message?: string }>
  syncAppMode: () => Promise<void>
}

export const useThemeStore = create<ThemeStore>()(
  persist(
    (set, get) => ({
      mode: 'light',
      color: 'zinc',
      appMode: 'live',
      isTogglingMode: false,

      setMode: (mode) => {
        // Light only: 'dark' is refused.
        if (mode !== 'light') return
        set({ mode })
      },

      setColor: () => {
        // One accent, the brand (src/sudarshan/theme.css). Kept so upstream callers compile.
      },

      setAppMode: (appMode) => {
        const previousMode = get().appMode
        set({ appMode })
        // Analyze mode is the light page with a violet accent and a banner, all
        // hung off the `analyzer` class (src/sudarshan/theme.css).
        applyModeClasses(appMode)
        // Notify listeners if mode changed
        if (previousMode !== appMode) {
          notifyModeChange(appMode)
        }
      },

      toggleMode: () => {
        // Light only: there is nothing to toggle to. Kept so upstream callers compile.
      },

      // Toggle app mode via backend API
      toggleAppMode: async (): Promise<{ success: boolean; message?: string }> => {
        if (get().isTogglingMode) return { success: false, message: 'Already toggling' }

        set({ isTogglingMode: true })
        try {
          // First fetch CSRF token
          const csrfResponse = await fetch('/auth/csrf-token', {
            credentials: 'include',
          })
          const csrfData = await csrfResponse.json()

          const response = await fetch('/auth/analyzer-toggle', {
            method: 'POST',
            credentials: 'include',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfData.csrf_token,
            },
          })

          const data = await response.json()

          if (response.ok && data.status === 'success') {
            const newMode: AppMode = data.data.analyze_mode ? 'analyzer' : 'live'
            get().setAppMode(newMode)
            return { success: true, message: data.data.message }
          } else {
            return { success: false, message: data.message || 'Failed to toggle mode' }
          }
        } catch (_error) {
          return { success: false, message: 'Network error' }
        } finally {
          set({ isTogglingMode: false })
        }
      },

      // Sync app mode from backend
      syncAppMode: async () => {
        try {
          const response = await fetch('/auth/analyzer-mode', {
            credentials: 'include',
          })

          if (response.ok) {
            const data = await response.json()
            if (data.status === 'success') {
              const backendMode: AppMode = data.data.analyze_mode ? 'analyzer' : 'live'
              const currentMode = get().appMode
              if (currentMode !== backendMode) {
                get().setAppMode(backendMode)
              }
            }
            // If backend returns error status but response.ok, keep current appMode
          }
          // If request fails (401, etc.) - user is logged out, keep current appMode
          // This preserves the theme across logout for visual continuity
        } catch (_error) {
          // On error, keep current appMode - preserves theme across logout
        }
      },
    }),
    {
      name: 'openalgo-theme',
      partialize: (state) => ({
        mode: state.mode,
        color: state.color,
        appMode: state.appMode, // Persist appMode for visual continuity across logout
      }),
      // A 'dark' or an accent saved by an earlier build is read back as light
      // with the brand accent.
      merge: (persisted, current) => ({
        ...current,
        ...(persisted as Partial<ThemeStore> | undefined),
        mode: 'light',
        color: 'zinc',
      }),
      onRehydrateStorage: () => (state) => {
        // Apply theme on rehydration
        if (state && typeof document !== 'undefined') {
          // Apply persisted appMode for visual continuity
          applyModeClasses(state.appMode)
          document.documentElement.setAttribute('data-theme', state.color)
        }
      },
    }
  )
)
