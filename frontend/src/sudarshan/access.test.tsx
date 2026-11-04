/**
 * The four states a screen that reads prices can be in (src/sudarshan/access.ts)
 * and the words that say them (AccessNotice).
 */
import { MemoryRouter } from 'react-router'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { syncSession } from '@/components/auth/AuthSync'
import { useAuthStore } from '@/stores/authStore'
import { AccessNotice } from './AccessNotice'
import { priceAccessOf } from './access'

function stubSession(body: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: status === 200, status, json: async () => body }))
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.getState().logout()
})

describe('priceAccessOf', () => {
  it('is checking, and claims nothing, until the server has answered', () => {
    expect(priceAccessOf({ apiKey: null, sessionChecked: false }).state).toBe('checking')
  })
  it('says Upstox is logged out when the server says the login lapsed, even with a key', () => {
    expect(
      priceAccessOf({ apiKey: 'k', sessionChecked: true, brokerSessionExpired: true }).state
    ).toBe('upstox-logged-out')
  })
  it('says no API key only after an answer with Upstox logged in', () => {
    expect(priceAccessOf({ apiKey: null, sessionChecked: true }).state).toBe('no-api-key')
  })
  it('is ready with a key', () => {
    expect(priceAccessOf({ apiKey: 'k', sessionChecked: true })).toEqual({
      state: 'ready',
      apiKey: 'k',
    })
  })
})

describe('AccessNotice', () => {
  const show = (state: 'checking' | 'upstox-logged-out' | 'no-api-key' | 'ready') =>
    render(
      <MemoryRouter>
        <AccessNotice
          access={state === 'ready' ? { state, apiKey: 'k' } : { state, apiKey: null }}
        />
      </MemoryRouter>
    )

  it('checking: a quiet line, never the API key message', () => {
    show('checking')
    expect(screen.getByText('Checking your login')).toBeInTheDocument()
    expect(screen.queryByText(/API key/)).toBeNull()
  })
  it('Upstox logged out: says so, with Auto login and Dashboard links', () => {
    show('upstox-logged-out')
    expect(screen.getByText(/Upstox is not logged in today/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Auto login' })).toHaveAttribute('href', '/autologin')
    expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', '/dashboard')
    expect(screen.queryByText(/needs an API key/)).toBeNull()
  })
  it('no API key: the existing message and link', () => {
    show('no-api-key')
    expect(screen.getByText('Prices and search need an API key.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Create one under API Key' })).toHaveAttribute(
      'href',
      '/apikey'
    )
  })
  it('ready: nothing', () => {
    const { container } = show('ready')
    expect(container).toBeEmptyDOMElement()
  })
})

describe('syncSession', () => {
  it('broker_session_expired with no api_key never lands on no-api-key', async () => {
    stubSession({
      status: 'success',
      logged_in: true,
      authenticated: true,
      user: 'owner',
      broker: 'upstox',
      broker_session_expired: true,
    })
    await syncSession()
    const { apiKey, sessionChecked, brokerSessionExpired } = useAuthStore.getState()
    expect(priceAccessOf({ apiKey, sessionChecked, brokerSessionExpired }).state).toBe(
      'upstox-logged-out'
    )
  })
  it('loads the key and clears the expiry once the login is back', async () => {
    stubSession({
      status: 'success',
      logged_in: true,
      authenticated: true,
      user: 'owner',
      broker: 'upstox',
      api_key: 'k',
    })
    await syncSession()
    expect(useAuthStore.getState().apiKey).toBe('k')
    expect(useAuthStore.getState().brokerSessionExpired).toBe(false)
  })
  it('keeps what it knew when the server cannot be read and keepOnFailure is set', async () => {
    useAuthStore.setState({ apiKey: 'k', sessionChecked: true })
    stubSession({}, 500)
    await syncSession({ keepOnFailure: true })
    expect(useAuthStore.getState().apiKey).toBe('k')
  })
  it('drops an answer that was asked before a logout', async () => {
    let answer: (value: unknown) => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((resolve) => {
            answer = resolve
          })
      )
    )
    const pending = syncSession({ keepOnFailure: true })
    useAuthStore.getState().logout()
    answer({
      ok: true,
      status: 200,
      json: async () => ({
        status: 'success',
        logged_in: true,
        authenticated: true,
        user: 'owner',
        broker: 'upstox',
        api_key: 'k',
      }),
    })
    await pending
    const { apiKey, user, isAuthenticated, sessionChecked } = useAuthStore.getState()
    expect(apiKey).toBeNull()
    expect(user).toBeNull()
    expect(isAuthenticated).toBe(false)
    expect(sessionChecked).toBe(false)
  })
  it('clears the saved key when a re-check says the account has none', async () => {
    useAuthStore.setState({ apiKey: 'old', sessionChecked: true })
    stubSession({
      status: 'success',
      logged_in: true,
      authenticated: true,
      user: 'owner',
      broker: 'upstox',
    })
    await syncSession({ keepOnFailure: true })
    expect(useAuthStore.getState().apiKey).toBeNull()
    expect(
      JSON.parse(localStorage.getItem('openalgo-auth') ?? '{}').state?.apiKey ?? null
    ).toBeNull()
  })
})
