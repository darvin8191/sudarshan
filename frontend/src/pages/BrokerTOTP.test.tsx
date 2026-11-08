/**
 * The broker TOTP step. After it succeeds the first screen must already know the
 * API key and that today's broker login is good, with no reload.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/authStore'
import { render, screen, userEvent, waitFor } from '@/test/test-utils'
import BrokerTOTP from './BrokerTOTP'

vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useParams: () => ({ broker: 'zerodha' }),
  useNavigate: () => vi.fn(),
}))

function respond(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    url: '',
    headers: { get: () => 'application/json' },
    json: async () => body,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.getState().logout()
})

describe('BrokerTOTP', () => {
  it('syncs the session after the broker login, so the API key is there at once', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/auth/csrf-token') return respond({ csrf_token: 'token' })
        if (url === '/zerodha/callback') return respond({ status: 'success' })
        if (url === '/auth/session-status')
          return respond({
            status: 'success',
            logged_in: true,
            authenticated: true,
            user: 'owner',
            broker: 'zerodha',
            api_key: 'key-from-server',
          })
        return respond({}, 404)
      })
    )
    const user = userEvent.setup()
    render(<BrokerTOTP />)
    await user.type(await screen.findByLabelText(/User ID/), 'AB1234')
    await user.type(screen.getByLabelText(/Password/), 'secret')
    await user.type(screen.getByLabelText(/TOTP/), '123456')
    await user.click(screen.getByRole('button', { name: /login|submit|authenticate|connect/i }))

    await waitFor(() => expect(useAuthStore.getState().apiKey).toBe('key-from-server'))
    expect(useAuthStore.getState().sessionChecked).toBe(true)
  })
})
