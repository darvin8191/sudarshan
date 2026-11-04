import { Navigate, Outlet } from 'react-router'
import { SocketProvider } from '@/components/socket/SocketProvider'
import { useAuthStore } from '@/stores/authStore'
import { AnalyzeBanner } from '@/sudarshan/AnalyzeBanner'
import { RiskBanner } from '@/sudarshan/risk/RiskBanner' // SUDARSHAN M20.7

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
      {/* SUDARSHAN M4.15: the visible height (dvh), not the layout viewport's
          (vh), which on a phone runs on under the address bar and pushes the
          bottom of a page (the chart's Buy and Sell) off the screen. A browser
          without dvh keeps h-screen. */}
      <div className="h-screen supports-[height:100dvh]:h-dvh bg-background flex flex-col overflow-hidden">
        <AnalyzeBanner />
        {/* SUDARSHAN M20.7: the kill switch, when it is on */}
        <RiskBanner />
        {/* The height the banner leaves, so a page sized h-full still ends at
            the bottom of the screen instead of past it. */}
        <div className="flex min-h-0 flex-1 flex-col">
          <Outlet />
        </div>
      </div>
    </SocketProvider>
  )
}
