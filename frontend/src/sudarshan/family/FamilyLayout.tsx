// SUDARSHAN P6 D2 (M2.7, M4.17): the family shell. White app bar with the lockup, the FAMILY ·
// VIEW ONLY badge and the member's initial, and the screen's title on its own line under them; its own bottom bar from `navigation.ts`; the
// session check against the viewer API's `me` on mount, on coming back to the page and on any
// "signed out" answer; the family market manager through `MarketDataContext`.
//
// What it never mounts: the owner's `Layout`, the Analyze banner, the kill-switch chip, the
// order ticket, Socket.IO. The owner's session checks stay out too (AuthSync skips /family/*).
//
// Review M5: a session that ended, here or on the server, forgets every member figure the page
// holds before the sign-in page shows; and no screen renders while the cache holds another
// member's figures (a session changed in another tab), so a shared phone never shows one member
// what was read for the one before.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ellipsis, WifiOff } from 'lucide-react'
import { useEffect, useLayoutEffect, useState } from 'react'
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
import { bare, FAMILY_HOME, familyMenu, familyTitle } from './navigation'
import {
  claimForMember,
  FamilyMeContext,
  filledForMember,
  forgetMember,
  type LoginState,
  ME_KEY,
  markSignedIn,
  wasSignedIn,
} from './session'

const LOGIN = '/family/login'

function isActive(pathname: string, href: string): boolean {
  const here = bare(pathname)
  const there = bare(href)
  return there === bare(FAMILY_HOME) ? here === there : here === there || here.startsWith(`${there}/`)
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
  usePushResync(session.data != null, session.data?.member.id)

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
    if (!signedOut) return
    getFamilyManager().disconnect()
    forgetMember(client, true) // the session's own query is the sign-in page's to clear
  }, [signedOut, client])
  // Close the sheet on every move.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the path is the trigger
  useEffect(() => setMore(false), [location.pathname])

  // The cache is this member's before any screen of theirs renders (review M5).
  const me = session.data ?? null
  const memberId = signedOut ? null : (me?.member.id ?? null)
  const [, claimed] = useState(0)
  useLayoutEffect(() => {
    if (memberId === null || filledForMember(client, memberId)) return
    claimForMember(client, memberId)
    claimed((n) => n + 1)
  }, [client, memberId])

  // A session is open here: remembered, so that when the server ends it the member is told why.
  useEffect(() => {
    if (memberId !== null) markSignedIn()
  }, [memberId])

  // Review M7: `/family` is outside the installed app's scope; the Dashboard lives at `/family/`.
  if (location.pathname === bare(FAMILY_HOME)) {
    return (
      <Navigate
        to={{ pathname: FAMILY_HOME, search: location.search, hash: location.hash }}
        replace
        state={location.state}
      />
    )
  }

  if (signedOut) {
    // Review M1: the service's own sentence (paused, removed, expired, signed out elsewhere) goes
    // to the sign-in page. A phone with no session to end gets the bare form.
    const state: LoginState | undefined =
      error?.code === 'signed_out' && wasSignedIn() ? { reason: error.message } : undefined
    return <Navigate to={LOGIN} replace state={state} />
  }
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

  if (!filledForMember(client, me.member.id)) return <PageLoader />

  const { bar, more: rest } = familyMenu(me)
  const title = familyTitle(location.pathname)
  const inMore = rest.some((item) => isActive(location.pathname, item.href))
  const initial = me.member.handle.slice(0, 1).toUpperCase()

  return (
    <MarketDataProvider manager={getFamilyManager()}>
      <FamilyMeContext.Provider value={me}>
        <div className="flex min-h-screen flex-col bg-background" data-family-shell>
          {/* Review H2 (v0.12.0): at 360 px the lockup, the badge and the initial fill the bar, so
              the title has a line of its own under it and is never cut; the badge is what gives
              way (it shrinks, and a child's takes its short form), and the initial stays in. */}
          <header className="sticky top-0 z-40 border-b bg-card">
            <div
              data-testid="family-bar"
              className="mx-auto flex h-[3.25rem] max-w-3xl items-center gap-2 px-3 pt-1"
            >
              <BrandLockup className="shrink-0" />
              <span aria-hidden="true" className="min-w-0 flex-1" />
              <FamilyBadge limited={me.limited} className="min-w-0 shrink" />
              <span
                role="img"
                aria-label={`Signed in as ${me.member.handle}`}
                className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[color:var(--primary-tint)] text-sm font-bold text-primary"
              >
                <span aria-hidden="true">{initial}</span>
              </span>
            </div>
            <div className="mx-auto max-w-3xl px-4 pb-2">
              <h1
                data-testid="family-title"
                className="sd-display text-[1.25rem] leading-tight font-bold break-words"
              >
                {title}
              </h1>
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

          {/* Review L8 (v0.12.0): the sheet's default line is "Details and actions", and this
              view has no action. */}
          <BottomSheet
            open={more}
            onOpenChange={setMore}
            title="More"
            hiddenDescription="More screens of the family view"
          >
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
