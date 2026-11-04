/**
 * The login screen (D look). The owner fills it from Chrome's password manager
 * after a fingerprint, which only works while the two fields keep their
 * autocomplete tokens, so those are pinned here along with the TOTP step.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor } from '@/test/test-utils'
import { useAuthStore } from '@/stores/authStore'
import Login from './Login'

interface FakeResponse {
  ok: boolean
  status: number
  url: string
  headers: { get: (name: string) => string | null }
  json: () => Promise<unknown>
}

function respond(body: unknown, status = 200): FakeResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    url: '',
    headers: { get: (name) => (name.toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => body,
  }
}

/** A server with no session yet, answering the login POST with `login`. */
function server(login: unknown) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      if (url === '/auth/check-setup') return respond({ needs_setup: false })
      if (url === '/auth/session-status') return respond({ status: 'error' }, 401)
      if (url === '/auth/csrf-token') return respond({ csrf_token: 'token' })
      if (url === '/auth/login') return respond(login)
      return respond({}, 404)
    })
  )
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Login', () => {
  it('keeps the autocomplete tokens that let Chrome fill both fields', async () => {
    server({ status: 'error' })
    render(<Login />)

    expect(await screen.findByLabelText('Username')).toHaveAttribute('autocomplete', 'username')
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'current-password')
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
  })

  it('heads the page with the name in both scripts and explains the fingerprint fill', async () => {
    server({ status: 'error' })
    render(<Login />)

    const heading = await screen.findByRole('heading', { level: 1 })
    expect(heading).toContainElement(screen.getByRole('img', { name: 'सुदर्शन' }))
    expect(heading).toHaveTextContent('SUDARSHAN')
    expect(
      screen.getByText('Saved password? Chrome fills it after your fingerprint.')
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Forgot password?' })).toHaveAttribute(
      'href',
      '/reset-password'
    )
  })

  it('shows and hides the password', async () => {
    server({ status: 'error' })
    const user = userEvent.setup()
    render(<Login />)

    await user.click(await screen.findByRole('button', { name: 'Show password' }))
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text')
    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
  })

  it('asks for the authenticator code when the server wants one, and goes back', async () => {
    const calls = server({ status: 'totp_required' })
    const user = userEvent.setup()
    render(<Login />)

    await user.type(await screen.findByLabelText('Username'), 'owner')
    await user.type(screen.getByLabelText('Password'), 'secret')
    await user.click(screen.getByRole('button', { name: 'Login' }))

    const code = await screen.findByLabelText('Authentication code')
    expect(code).toHaveAttribute('autocomplete', 'one-time-code')
    expect(code).toHaveFocus()
    expect(calls).toContain('/auth/login')
    expect(screen.getByRole('button', { name: 'Verify code' })).toBeDisabled()

    await user.type(code, '12ab3456')
    expect(code).toHaveValue('123456')
    expect(screen.getByRole('button', { name: 'Verify code' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: 'Back' }))
    await waitFor(() => expect(screen.getByLabelText('Password')).toBeInTheDocument())
  })

  it('shows the server message when the password is refused', async () => {
    server({ status: 'error', message: 'Invalid username or password' })
    const user = userEvent.setup()
    render(<Login />)

    await user.type(await screen.findByLabelText('Username'), 'owner')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Login' }))

    expect(await screen.findByText('Invalid username or password')).toBeInTheDocument()
  })
})

/**
 * A server whose session check answers 401 until a login succeeds, then the
 * resumed Upstox session with the API key, as it does on a morning login.
 */
function morningServer(loginBody: unknown) {
  let signedIn = false
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/auth/check-setup') return respond({ needs_setup: false })
      if (url === '/auth/csrf-token') return respond({ csrf_token: 'token' })
      if (url === '/auth/login') return respond(loginBody)
      if (url === '/auth/login/totp') {
        signedIn = true
        return respond({ status: 'success', broker: 'upstox', redirect: '/dashboard' })
      }
      if (url === '/auth/session-status') {
        if (!signedIn) return respond({ status: 'error' }, 401)
        return respond({
          status: 'success',
          authenticated: true,
          logged_in: true,
          user: 'owner',
          broker: 'upstox',
          api_key: 'key-from-server',
        })
      }
      return respond({}, 404)
    })
  )
  return {
    signIn: () => {
      signedIn = true
    },
  }
}

describe('Login then the API key, with no reload', () => {
  it('loads the API key after the password step resumes the Upstox session', async () => {
    const server = morningServer({ status: 'success', broker: 'upstox', redirect: '/dashboard' })
    useAuthStore.getState().logout()
    const user = userEvent.setup()
    render(<Login />)
    await user.type(await screen.findByLabelText('Username'), 'owner')
    await user.type(screen.getByLabelText('Password'), 'secret')
    server.signIn()
    await user.click(screen.getByRole('button', { name: 'Login' }))

    await waitFor(() => expect(useAuthStore.getState().apiKey).toBe('key-from-server'))
    expect(useAuthStore.getState().sessionChecked).toBe(true)
    expect(useAuthStore.getState().brokerSessionExpired).toBe(false)
  })

  it('loads the API key after the authenticator code step', async () => {
    morningServer({ status: 'totp_required' })
    useAuthStore.getState().logout()
    const user = userEvent.setup()
    render(<Login />)
    await user.type(await screen.findByLabelText('Username'), 'owner')
    await user.type(screen.getByLabelText('Password'), 'secret')
    await user.click(screen.getByRole('button', { name: 'Login' }))
    await user.type(await screen.findByLabelText('Authentication code'), '123456')
    await user.click(screen.getByRole('button', { name: 'Verify code' }))

    await waitFor(() => expect(useAuthStore.getState().apiKey).toBe('key-from-server'))
    expect(useAuthStore.getState().sessionChecked).toBe(true)
  })
})
