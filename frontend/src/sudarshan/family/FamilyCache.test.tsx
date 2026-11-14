// Review M5: a shared phone never shows one member what was read for the member before. Member A's
// positions are on screen; their session ends (on the server, or in another tab where member B
// then signs in); B's own read of the books is held, so whatever renders comes from the cache.
// Through the real shell, sign-in page and positions screen. Synthetic rows and names only.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PRICE_SCALE } from '@/sudarshan/portfolio/types'
import { announceSignedOut } from './api'
import { FamilyLayout } from './FamilyLayout'
import { resetFamilyManagerForTests } from './familyManager'
import FamilyLogin from './pages/FamilyLogin'
import FamilyPositions from './pages/FamilyPositions'
import { bookOf, FakeSocket, fail, meOf, ok } from './testing/fixtures'
import type { Me } from './types'

const member = (id: number, handle: string): Me =>
  meOf('viewer', {
    member: { id, handle, display_name: `Member ${id}`, role: 'viewer', language: 'en' },
  })
const A = member(1, 'member_one')
const B = member(2, 'member_two')
const ROW_OF_A = {
  exchange: 'NSE',
  symbol: 'ONLYA',
  product: 'MIS',
  side: 'long',
  last_price_u: 205 * PRICE_SCALE,
  close_price_u: 200 * PRICE_SCALE,
}

/** The service as the phone meets it: who the cookie is, and B's book read held forever. */
function service() {
  const state = { who: 'A' as 'A' | 'B' | 'nobody', bookCalls: 0 }
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url =
        typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
      if (url.startsWith('/family/api/me')) {
        if (state.who === 'nobody') {
          return json(fail('signed_out', 'You were signed out on this device.'), 401)
        }
        return json(ok(state.who === 'A' ? A : B))
      }
      if (url.startsWith('/family/api/login')) {
        state.who = 'B'
        return json(ok(B))
      }
      if (url.startsWith('/family/api/books/positions')) {
        state.bookCalls += 1
        if (state.who === 'A') return json(ok(bookOf('positions', [ROW_OF_A])))
        return new Promise<Response>(() => undefined) // B's read is on its way, and stays so
      }
      return json(fail('not_found', 'No such page.'), 404)
    })
  )
  return state
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/family']}>
        <Routes>
          <Route path="/family/login" element={<FamilyLogin />} />
          <Route path="/family" element={<FamilyLayout />}>
            <Route index element={<FamilyPositions />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
  return client
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
})

afterEach(() => {
  cleanup()
  resetFamilyManagerForTests()
  vi.unstubAllGlobals()
})

describe('one member never sees what was read for another (review M5)', () => {
  it('a session the server ended leaves no row of it for the next member who signs in', async () => {
    const state = service()
    const client = mount()
    expect(await screen.findByText('ONLYA')).toBeInTheDocument()
    state.who = 'nobody' // the server ended A's session; a socket closed for it says so
    act(() => announceSignedOut())
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(client.getQueryCache().findAll({ queryKey: ['family', 'book'] })).toEqual([])
    await userEvent.type(screen.getByLabelText('Sign-in name'), 'member_two')
    await userEvent.type(screen.getByLabelText('Password'), 'a long synthetic passphrase')
    await userEvent.type(screen.getByLabelText('6-digit code'), '123456')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('img', { name: 'Signed in as member_two' })).toBeInTheDocument()
    expect(state.bookCalls).toBe(2) // B's own read went out, and is held
    expect(screen.queryByText('ONLYA')).toBeNull()
  })

  it('a session changed in another tab shows no row of the member before', async () => {
    const state = service()
    mount()
    expect(await screen.findByText('ONLYA')).toBeInTheDocument()
    state.who = 'B' // A signed out in another tab, and B signed in there
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(await screen.findByRole('img', { name: 'Signed in as member_two' })).toBeInTheDocument()
    expect(screen.queryByText('ONLYA')).toBeNull()
    expect(state.bookCalls).toBe(2)
  })
})
