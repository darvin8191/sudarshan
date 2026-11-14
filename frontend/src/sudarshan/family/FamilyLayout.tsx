// SUDARSHAN P6 D2 (M2.7, M4.17): the family shell. White app bar with the compact lockup, the
// screen's title and the FAMILY · VIEW ONLY badge; its own bottom bar from `navigation.ts`; the
// session check against the viewer API's `me` on mount, on coming back to the page and on any
// "signed out" answer; the family market manager through `MarketDataContext`.
//
// What it never mounts: the owner's `Layout`, the Analyze banner, the kill-switch chip, the
// order ticket, Socket.IO. The owner's session checks stay out too (AuthSync skips /family/*).
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ellipsis, WifiOff } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, Navigate, Outlet, useLocation } from 'react-router'
import { PageLoader } from '@/components/ui/page-loader'
import { MarketDataProvider } from '@/contexts/MarketDataContext'
import { cn } from '@/lib/utils'
import { BrandLockup } from '@/sudarshan/BrandLockup'
import { usePushResync } from '@/sudarshan/pwa/push'
import { registerFamilyWorker } from '@/sudarshan/pwa/register'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { FamilyError, familyApi, onSignedOut } from './api'
import { FamilyBadge } from './FamilyBadge'
import { getFamilyManager } from './familyManager'
import { familyMenu, familyTitle } from './navigation'
import { FamilyMeContext, ME_KEY } from './session'

const LOGIN = '/family/login'

function isActive(pathname: string, href: string): boolean {
  const here = pathname.replace(/\/$/, '') || '/'
  return href === '/family' ? here === '/family' : here === href || here.startsWith(`${href}/`)
}

/** The phone can not reach the service and there is no earlier answer to keep showing. */
function Unreachable({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-4">
      <section
        role="alert"
        className="flex max-w-sm flex-col items-center gap-3 rounded-[1.125rem] border bg-card px-6 py-8 text-center shadow-[var(--shadow-card)]"
      >
        <WifiOff aria-hidden="true" className="size-6 text-[color:var(--warning-text)]" />
        <p className="text-base font-bold">{message}</p>
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex min-h-12 items-center justify-center rounded-[0.875rem] bg-primary px-5 text-[0.9375rem] font-semibold text-primary-foreground"
        >
          Try again
        </button>
      </section>
    </div>
  )
}

export function FamilyLayout() {
  const location = useLocation()
  const client = useQueryClient()
  const [more, setMore] = useState(false)

  // The worker is registered from here and nowhere else (Y26).
  useEffect(() => {
    void registerFamilyWorker()
  }, [])

  const session = useQuery({
    queryKey: ME_KEY,
    queryFn: ({ signal }) => familyApi.me(signal),
    retry: false,
    staleTime: 60_000,
    // The checks that matter are made below, on every return to the page, stale or not.
    refetchOnWindowFocus: false,
    refetchOnReconnect: true,
  })

  // SUDARSHAN P6 D8: on every open, a granted permission re-posts the subscription the browser
  // holds (Chrome does not always say when it changes).
  usePushResync(session.data != null)

  // Coming back to the page (the phone woke, the app was switched back to) checks the session.
  useEffect(() => {
    const visible = () => {
      if (document.visibilityState === 'visible') {
        void client.invalidateQueries({ queryKey: ME_KEY })
      }
    }
    document.addEventListener('visibilitychange', visible)
    return () => document.removeEventListener('visibilitychange', visible)
  }, [client])

  // Any "signed out" answer, or a socket closed for it, checks the session again at once.
  useEffect(() => onSignedOut(() => void client.invalidateQueries({ queryKey: ME_KEY })), [client])

  const error = session.error instanceof FamilyError ? session.error : null
  const signedOut = error?.code === 'signed_out' || error?.code === 'signin_failed'
  useEffect(() => {
    if (signedOut) getFamilyManager().disconnect()
  }, [signedOut])
  // Close the sheet on every move.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the path is the trigger
  useEffect(() => setMore(false), [location.pathname])

  if (signedOut) return <Navigate to={LOGIN} replace />
  const me = session.data ?? null
  if (!me) {
    if (session.isPending) return <PageLoader />
    if (error?.code === 'off') {
      return <Unreachable message={error.message} onRetry={() => void session.refetch()} />
    }
    return (
      <Unreachable
        message={error?.message ?? 'The family view cannot be reached right now.'}
        onRetry={() => void session.refetch()}
      />
    )
  }

  const { bar, more: rest } = familyMenu(me)
  const title = familyTitle(location.pathname)
  const inMore = rest.some((item) => isActive(location.pathname, item.href))
  const initial = me.member.handle.slice(0, 1).toUpperCase()

  return (
    <MarketDataProvider manager={getFamilyManager()}>
      <FamilyMeContext.Provider value={me}>
        <div className="flex min-h-screen flex-col bg-background" data-family-shell>
          <header className="sticky top-0 z-40 border-b bg-card">
            <div className="mx-auto flex h-14 max-w-3xl items-center gap-2 px-3">
              <BrandLockup />
              <h1 className="sd-display min-w-0 flex-1 truncate text-[1.125rem] font-bold">
                {title}
              </h1>
              <FamilyBadge limited={me.limited} />
              <span
                role="img"
                aria-label={`Signed in as ${me.member.handle}`}
                className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[color:var(--primary-tint)] text-sm font-bold text-primary"
              >
                <span aria-hidden="true">{initial}</span>
              </span>
            </div>
          </header>

          <main className="sd-page mx-auto w-full max-w-3xl flex-1 px-4 pt-4 pb-[calc(6.5rem+env(safe-area-inset-bottom))]">
            <Outlet />
          </main>

          <nav
            aria-label="Family"
            className="safe-area-bottom fixed right-0 bottom-0 left-0 z-50 border-t bg-card"
          >
            <div
              className="mx-auto grid h-[4.75rem] max-w-3xl px-1"
              style={{ gridTemplateColumns: `repeat(${bar.length + 1}, minmax(0, 1fr))` }}
            >
              {bar.map((item) => {
                const active = isActive(location.pathname, item.href)
                return (
                  <Link
                    key={item.href}
                    to={item.href}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'flex min-h-12 flex-col items-center justify-center gap-1 rounded-xl text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      active ? 'font-bold text-primary' : 'font-semibold text-muted-foreground'
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className={cn(
                        'flex h-[1.875rem] w-14 items-center justify-center rounded-full',
                        active && 'bg-[color:var(--primary-tint)]'
                      )}
                    >
                      <item.icon className="size-[1.375rem]" strokeWidth={active ? 2.1 : 1.9} />
                    </span>
                    <span className="leading-none">{item.label}</span>
                  </Link>
                )
              })}
              <button
                type="button"
                aria-haspopup="dialog"
                onClick={() => setMore(true)}
                className={cn(
                  'flex min-h-12 flex-col items-center justify-center gap-1 rounded-xl text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  inMore ? 'font-bold text-primary' : 'font-semibold text-muted-foreground'
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    'flex h-[1.875rem] w-14 items-center justify-center rounded-full',
                    inMore && 'bg-[color:var(--primary-tint)]'
                  )}
                >
                  <Ellipsis className="size-[1.375rem]" />
                </span>
                <span className="leading-none">More</span>
              </button>
            </div>
          </nav>

          <BottomSheet open={more} onOpenChange={setMore} title="More">
            <ul className="flex flex-col gap-1">
              {rest.map((item) => (
                <li key={item.href}>
                  <Link
                    to={item.href}
                    aria-current={isActive(location.pathname, item.href) ? 'page' : undefined}
                    className="flex min-h-12 items-center gap-3 rounded-[0.875rem] px-3 text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <item.icon
                      aria-hidden="true"
                      className="size-5 text-[color:var(--text-secondary)]"
                    />
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </BottomSheet>
        </div>
      </FamilyMeContext.Provider>
    </MarketDataProvider>
  )
}
