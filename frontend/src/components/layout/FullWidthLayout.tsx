import { Navigate, Outlet } from 'react-router'
import { SocketProvider } from '@/components/socket/SocketProvider'
import { useAuthStore } from '@/stores/authStore'
import { AnalyzeBanner } from '@/sudarshan/AnalyzeBanner'

/**
 * Full-width layout for apps like Playground that need maximum screen space.
 * No container constraints, minimal chrome.
 */
export function FullWidthLayout() {
  const { isAuthenticated, user } = useAuthStore()

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  if (!user?.broker) {
    return <Navigate to="/broker" replace />
  }

  return (
    <SocketProvider>
      <div className="h-screen bg-background flex flex-col overflow-hidden">
        <AnalyzeBanner />
        {/* The height the banner leaves, so a page sized h-full still ends at
            the bottom of the screen instead of past it. */}
        <div className="flex min-h-0 flex-1 flex-col">
          <Outlet />
        </div>
      </div>
    </SocketProvider>
  )
}
