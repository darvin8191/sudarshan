import { Navigate, Outlet } from 'react-router'
import { SocketProvider } from '@/components/socket/SocketProvider'
import { useAuthStore } from '@/stores/authStore'
import { AnalyzeBanner } from '@/sudarshan/AnalyzeBanner'
import { RiskBanner } from '@/sudarshan/risk/RiskBanner' // SUDARSHAN M20.7
import { Footer } from './Footer'
import { MobileBottomNav } from './MobileBottomNav'
import { Navbar } from './Navbar'

export function Layout() {
  const { isAuthenticated, user } = useAuthStore()

  // AuthSync has already synced Flask session with Zustand store
  // So we just need to check the Zustand store state
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  // If logged in but no broker selected, redirect to broker selection
  if (!user?.broker) {
    return <Navigate to="/broker" replace />
  }

  return (
    <SocketProvider>
      <div className="min-h-screen bg-background flex flex-col">
        {/* One sticky header, so the Analyze banner stays in view with the navbar. */}
        <div className="sticky top-0 z-50">
          <AnalyzeBanner />
          {/* SUDARSHAN M20.7: the kill switch, when it is on */}
          <RiskBanner />
          <Navbar />
        </div>
        {/* SUDARSHAN: on a phone the page starts closer to the bar (D), and
            ends clear of the 76px bottom bar and any gesture-bar inset. */}
        <main className="sd-page container mx-auto flex-1 px-4 pt-4 pb-[calc(6rem+env(safe-area-inset-bottom))] md:py-6">
          <Outlet />
        </main>
        <Footer className="hidden md:block" />
        <MobileBottomNav />
      </div>
    </SocketProvider>
  )
}

export function PublicLayout() {
  return (
    <div className="min-h-screen bg-background">
      <Outlet />
    </div>
  )
}
