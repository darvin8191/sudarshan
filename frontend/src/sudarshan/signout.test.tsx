/**
 * SUDARSHAN, release review v0.8.0 M2: the app keeps the API key in the browser's storage
 * (stores/authStore.ts persists it), and a logout or a password change does not change the key.
 * Every place that learns the session has ended forgets it at once. Before, a refused session
 * (a read answered 401, a live connection refused for a session that had ended) and the error
 * page's logout went to the login page and left the key stored, for the next page load's check to
 * clear, which it did only when that check reached the server.
 *
 * L6: the owner's own logout says so when the server could not sign the other devices out.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient, webClient } from '@/api/client'
import ServerError from '@/pages/ServerError'
import { useAuthStore } from '@/stores/authStore'
import { forgetApiKey, logoutWords } from './signout'

const KEY = 'test-api-key-0123456789abcdef'

function storedKey(): string | null | undefined {
  const raw = window.localStorage.getItem('openalgo-auth')
  return raw ? (JSON.parse(raw).state?.apiKey ?? null) : undefined
}

function signedIn() {
  useAuthStore.getState().login('owner', 'upstox')
  useAuthStore.getState().setApiKey(KEY)
  expect(storedKey()).toBe(KEY)
}

// jsdom forbids assigning window.location, so swap in a writable stand-in.
const realLocation = window.location
beforeEach(() => {
  Object.defineProperty(window, 'location', {
    value: { href: '' },
    writable: true,
    configurable: true,
  })
  window.localStorage.clear()
})
afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.getState().logout()
})
afterAll(() => {
  Object.defineProperty(window, 'location', { value: realLocation, configurable: true })
})

/** Run a failed answer through an axios instance's response interceptors. */
async function answered401(instance: typeof webClient) {
  const handlers = instance.interceptors.response as unknown as {
    handlers: Array<{ rejected?: (error: unknown) => Promise<unknown> }>
  }
  for (const handler of handlers.handlers) {
    if (handler?.rejected) {
      await handler.rejected({ response: { status: 401, data: {} }, message: 'x' }).catch(() => {})
    }
  }
}

describe('the API key kept in the browser is forgotten at every sign-out', () => {
  it('forgetApiKey removes it from the store and from the browser storage', () => {
    signedIn()
    forgetApiKey()
    expect(useAuthStore.getState().apiKey).toBeNull()
    expect(storedKey()).toBeNull()
  })

  it.each([
    ['a session read', webClient],
    ['an API read', apiClient],
  ])('when %s is answered 401, before it goes to the login page', async (_what, instance) => {
    signedIn()
    await answered401(instance)
    expect(window.location.href).toBe('/login')
    expect(useAuthStore.getState().apiKey).toBeNull()
    expect(storedKey()).toBeNull()
  })

  it('when the error page logs out', async () => {
    signedIn()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/auth/csrf-token'
          ? ({ ok: true, json: async () => ({ csrf_token: 't' }) } as Response)
          : ({ ok: true, json: async () => ({ status: 'success' }) } as Response)
      )
    )
    render(
      <MemoryRouter>
        <ServerError />
      </MemoryRouter>
    )
    await userEvent.click(screen.getByRole('button', { name: /logout/i }))
    await vi.waitFor(() => expect(window.location.href).toBe('/login'))
    expect(storedKey()).toBeNull()
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
  })
})

describe('the toast after the owner logs out (review L6)', () => {
  it('says the server sentence when the other devices could not be signed out', () => {
    const said =
      'You are logged out here, but your other devices could not be signed out. Log in and log out once more to sign them out.'
    expect(logoutWords({ status: 'error', message: said })).toEqual({ kind: 'error', text: said })
  })

  it.each([
    [{ status: 'success', message: 'Logged out successfully' }],
    [undefined],
    [{ status: 'error' }],
  ])('says logged out otherwise (%j)', (answer) => {
    expect(logoutWords(answer)).toEqual({ kind: 'success', text: 'Logged out successfully' })
  })
})
