/**
 * The loop the SIO1 re-review found: after a logout on another device, force_logout signs the
 * phone's store out and the shell's guard sends it to the login page; the page asked the server,
 * which still took the phone's cookie as signed in, and sent it to the dashboard, whose guard sent
 * it straight back: about 300 session reads in two seconds, until the tab was reloaded.
 *
 * Now the page asks with ?for=login, and the server answers a cookie whose session row it removed
 * as signed out (sudarshan/alerts/tests/test_socketio_owner.py pins that side); and whatever the
 * server answers, the page sends a tab to the dashboard at most once a minute (sudarshan/trips.ts).
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Navigate, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Login from '@/pages/Login'
import { useAuthStore } from '@/stores/authStore'
import { resetTripsForTests } from '@/sudarshan/trips'

/** The shells' guard (Layout, FullWidthLayout): a signed-out store goes to the login page. */
function Guard() {
  const { isAuthenticated } = useAuthStore()
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <div>dashboard</div>
}

const SIGNED_IN = {
  status: 'success',
  authenticated: true,
  logged_in: true,
  user: 'owner',
  broker: 'upstox',
}
const SIGNED_OUT = { status: 'success', authenticated: false, logged_in: false }

/** A server that answers the login page's question (?for=login) with `atLogin`, any other read as signed in. */
function server(atLogin: object): string[] {
  const reads: string[] = []
  vi.stubGlobal('fetch', async (url: string) => {
    const address = String(url)
    if (address.includes('session-status')) reads.push(address)
    const body = address.includes('check-setup')
      ? { needs_setup: false }
      : address.includes('for=login')
        ? atLogin
        : SIGNED_IN
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  return reads
}

function openLoginAfterForceLogout() {
  useAuthStore.getState().logout() // what force_logout does
  render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/dashboard" element={<Guard />} />
      </Routes>
    </MemoryRouter>
  )
}

/** This file watches the two pages trade places over real time, as a browser runs them, so React's
 * own scheduler applies each update: act() would hold them all until the wait ends. */
type ActFlag = { IS_REACT_ACT_ENVIRONMENT?: boolean }
let actEnvironment: boolean | undefined

beforeEach(() => {
  resetTripsForTests()
  actEnvironment = (globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT
  ;(globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = false
})

afterEach(() => {
  ;(globalThis as ActFlag).IS_REACT_ACT_ENVIRONMENT = actEnvironment
  vi.unstubAllGlobals()
})

/** Long enough for hundreds of trips between the two pages, as the loop made. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 1_000))

const AT_LOGIN = '/auth/session-status?for=login'

describe('the login page after a logout on another device', () => {
  it('shows the form, with one read, when the server has signed the cookie out', async () => {
    const reads = server(SIGNED_OUT)
    openLoginAfterForceLogout()

    await settle()
    expect(reads).toEqual([AT_LOGIN])
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
  })

  it('sends the tab to the dashboard at most once a minute, whatever the server answers', async () => {
    const reads = server(SIGNED_IN) // a server that still takes the cookie as signed in
    openLoginAfterForceLogout()

    await settle()
    expect(reads).toEqual([AT_LOGIN, AT_LOGIN]) // one trip to the dashboard and back
    expect(screen.getByLabelText('Username')).toBeInTheDocument() // then the form
  })
})
