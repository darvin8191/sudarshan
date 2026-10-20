/**
 * SUDARSHAN (M2.20) Auto login page: secrets are write-only, inputs are phone friendly, status is
 * never told by colour alone, and the actions call the right endpoints.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AutoLoginStatus } from '@/api/autologin'
import { fireEvent, render, screen, waitFor, within } from '@/test/test-utils'

const api = vi.hoisted(() => ({
  status: vi.fn(),
  saveTotpSecret: vi.fn(),
  clearTotpSecret: vi.fn(),
  savePin: vi.fn(),
  clearPin: vi.fn(),
  setEnabled: vi.fn(),
  testNow: vi.fn(),
}))
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('@/api/autologin', () => ({ autologinApi: api }))
vi.mock('@/utils/toast', () => ({ showToast: toast }))

import AutoLogin, { normalizeTotpKey } from './AutoLogin'

// Fake values only.
const FAKE_KEY = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
const FAKE_PIN = '246813'

function makeStatus(overrides: Partial<AutoLoginStatus> = {}): AutoLoginStatus {
  return {
    enabled: false,
    broker_supported: true,
    connected_today: false,
    secrets: {
      totp_secret: { set: false, updated_at: null, readable: false },
      pin: { set: false, updated_at: null, readable: false },
    },
    paused: null,
    last_run: null,
    last_success_at: null,
    running: false,
    next_run: null,
    schedule: ['08:35', '08:45', '08:55'],
    test: { allowed: false, reason: 'The TOTP key or the PIN is not saved yet.' },
    recent: [],
    ...overrides,
  }
}

const SAVED = {
  totp_secret: { set: true, updated_at: '2026-10-20T08:00:00+05:30', readable: true },
  pin: { set: true, updated_at: '2026-10-20T08:00:05+05:30', readable: true },
}

function renderPage(ui: ReactElement = <AutoLogin />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  api.status.mockResolvedValue(makeStatus())
  api.saveTotpSecret.mockResolvedValue('TOTP key saved.')
  api.savePin.mockResolvedValue('PIN saved.')
  api.clearTotpSecret.mockResolvedValue('TOTP key removed.')
  api.clearPin.mockResolvedValue('PIN removed.')
  api.setEnabled.mockResolvedValue('Auto-login is on.')
})

describe('Auto login page', () => {
  it('shows the not-set-up state with labels, not colour alone', async () => {
    renderPage()
    expect(await screen.findByRole('heading', { name: /Auto login/ })).toBeInTheDocument()
    expect(screen.getByText('Off')).toBeInTheDocument()
    expect(screen.getByText('None while auto-login is off')).toBeInTheDocument()
    expect(screen.getByText('TOTP key: not saved')).toBeInTheDocument()
    expect(screen.getByText('PIN: not saved')).toBeInTheDocument()
    expect(screen.getByText('Not connected yet today')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Test now/ })).toBeDisabled()
    expect(screen.getByText('The TOTP key or the PIN is not saved yet.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('explains where the TOTP key is, in English and in Gujarati', async () => {
    const { container } = renderPage()
    await screen.findByRole('heading', { name: /Auto login/ })
    expect(
      screen.getByText(/Profile \(Account settings\) > Security \(2FA\) > TOTP\./)
    ).toBeInTheDocument()
    expect(screen.getByText(/re-generated there/)).toBeInTheDocument()
    const gujarati = container.querySelector('[lang="gu"]')
    expect(gujarati?.textContent).toContain('TOTP key')
    expect(gujarati?.textContent).toMatch(/[\u0A80-\u0AFF]/) // Gujarati script
  })

  it('keeps the TOTP key masked until asked, and paste friendly', async () => {
    renderPage()
    const key = await screen.findByLabelText('TOTP key')
    expect(key).toHaveAttribute('type', 'password')
    expect(key).toHaveAttribute('autocomplete', 'off')
    expect(key).toHaveAttribute('autocapitalize', 'characters')
    expect(key).toHaveAttribute('spellcheck', 'false')
    fireEvent.click(screen.getByRole('button', { name: 'Show TOTP key' }))
    expect(key).toHaveAttribute('type', 'text')
    fireEvent.click(screen.getByRole('button', { name: 'Hide TOTP key' }))
    expect(key).toHaveAttribute('type', 'password')
  })

  it('takes only 6 digits for the PIN, masked, with a number pad', async () => {
    renderPage()
    const pin = await screen.findByLabelText('PIN')
    expect(pin).toHaveAttribute('type', 'password')
    expect(pin).toHaveAttribute('inputmode', 'numeric')
    fireEvent.change(pin, { target: { value: '12a45' } })
    expect(pin).toHaveValue('1245')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.getByText('The PIN has exactly 6 digits.')).toBeInTheDocument()
    fireEvent.change(pin, { target: { value: '1234567' } })
    expect(pin).toHaveValue('123456')
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it('refuses a key that is not base32', async () => {
    renderPage()
    fireEvent.change(await screen.findByLabelText('TOTP key'), {
      target: { value: 'not-a-key-1890' },
    })
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.getByText(/16 to 64 characters/)).toBeInTheDocument()
  })

  it('saves the normalised key and the PIN, then forgets both', async () => {
    const { container } = renderPage()
    const spaced = 'gezd gnbv gy3t qojq gezd gnbv gy3t qojq'
    fireEvent.change(await screen.findByLabelText('TOTP key'), { target: { value: spaced } })
    fireEvent.change(screen.getByLabelText('PIN'), { target: { value: FAKE_PIN } })
    api.status.mockResolvedValue(makeStatus({ secrets: SAVED }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api.savePin).toHaveBeenCalledWith(FAKE_PIN))
    expect(api.saveTotpSecret).toHaveBeenCalledWith(FAKE_KEY)
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('TOTP key and PIN saved.'))
    expect(await screen.findByText(/TOTP key: saved 20 Oct/)).toBeInTheDocument()
    expect(screen.getByLabelText('New TOTP key')).toHaveValue('')
    expect(screen.getByLabelText('New PIN')).toHaveValue('')
    const page = container.innerHTML
    expect(page).not.toContain(FAKE_KEY)
    expect(page).not.toContain(FAKE_PIN)
    expect(page.toLowerCase()).not.toContain('gezd')
  })

  it('removes a saved secret', async () => {
    api.status.mockResolvedValue(makeStatus({ secrets: SAVED }))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Remove the saved TOTP key' }))
    await waitFor(() => expect(api.clearTotpSecret).toHaveBeenCalledTimes(1))
    expect(api.clearPin).not.toHaveBeenCalled()
  })

  it('warns when a saved secret can no longer be read', async () => {
    api.status.mockResolvedValue(
      makeStatus({ secrets: { ...SAVED, pin: { ...SAVED.pin, readable: false } } })
    )
    renderPage()
    expect(await screen.findByText(/PIN: saved, but it can no longer be read/)).toBeInTheDocument()
  })

  it('switches auto-login on', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('switch', { name: /Log in automatically/ }))
    await waitFor(() => expect(api.setEnabled).toHaveBeenCalled())
    expect(api.setEnabled.mock.calls[0][0]).toBe(true)
  })

  it('runs Test now and shows the result', async () => {
    api.status.mockResolvedValue(
      makeStatus({ enabled: true, secrets: SAVED, test: { allowed: true, reason: null } })
    )
    let finish: (value: unknown) => void = () => {}
    api.testNow.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Test now/ }))
    expect(await screen.findByRole('button', { name: /Testing/ })).toBeDisabled()
    finish({ outcome: 'ok', message: 'Logged in to Upstox.', at: '2026-10-20T19:05:00+05:30' })
    expect(await screen.findByText('Logged in to Upstox.')).toBeInTheDocument()
    expect(toast.success).toHaveBeenCalledWith('Upstox auto-login test passed.')
    expect(api.testNow).toHaveBeenCalledTimes(1)
  })

  it('shows why Test now was refused', async () => {
    api.status.mockResolvedValue(
      makeStatus({ secrets: SAVED, test: { allowed: true, reason: null } })
    )
    api.testNow.mockRejectedValue(
      new Error(
        'Not available between 09:15 and 15:30 while Upstox is already connected for today.'
      )
    )
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Test now/ }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining('Not available between 09:15')
      )
    )
  })

  it('shows a failed scheduled run with icon, label and plain message', async () => {
    api.status.mockResolvedValue(
      makeStatus({
        enabled: true,
        secrets: SAVED,
        next_run: '2026-10-21T08:35:00+05:30',
        last_run: {
          at: '2026-10-20T08:35:10+05:30',
          kind: 'scheduled',
          outcome: 'failed',
          message: 'Could not reach Upstox.',
        },
        recent: [
          {
            at: '2026-10-20T08:35:10+05:30',
            kind: 'scheduled',
            outcome: 'failed',
            message: 'Could not reach Upstox.',
          },
        ],
      })
    )
    renderPage()
    const lastRun = (await screen.findByText('Last run')).parentElement as HTMLElement
    expect(within(lastRun).getByText('Failed')).toBeInTheDocument()
    expect(within(lastRun).getByText('Could not reach Upstox.')).toBeInTheDocument()
    expect(screen.getByText(/21 Oct.*08:35 IST/)).toBeInTheDocument()
    expect(screen.getByText('Recent attempts')).toBeInTheDocument()
  })

  it('shows a pause once, says how to end it, and keeps Test now waiting', async () => {
    const refusal =
      'Auto-login is paused because Upstox refused the last login. Save the TOTP key or the PIN again first, so the same ones are not sent to Upstox twice.'
    api.status.mockResolvedValue(
      makeStatus({
        enabled: true,
        secrets: SAVED,
        paused: { message: 'Upstox did not accept the saved TOTP key or PIN.' },
        test: { allowed: false, reason: refusal },
      })
    )
    renderPage()
    const banner = (await screen.findByText('Auto-login is paused')).closest(
      '[role="alert"]'
    ) as HTMLElement
    expect(
      within(banner).getByText('Upstox did not accept the saved TOTP key or PIN.')
    ).toBeVisible()
    expect(within(banner).getByText(/save the TOTP key or the PIN again/)).toBeVisible()
    expect(within(banner).getByText(/off and on does not resume it/)).toBeVisible()
    expect(banner.textContent?.match(/paused/g)).toHaveLength(1)
    expect(screen.getByText('None while paused')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Test now/ })).toBeDisabled()
    expect(screen.getByText(refusal)).toBeInTheDocument()
  })

  it('says so when the settings cannot be loaded', async () => {
    api.status.mockRejectedValue(new Error('Network Error'))
    renderPage()
    expect(await screen.findByText('Could not load the auto-login settings')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('normalizeTotpKey', () => {
  it('drops spaces and padding and upper-cases, as the server does', () => {
    expect(normalizeTotpKey(' gezd gnbv\ngy3t qojq== ')).toBe('GEZDGNBVGY3TQOJQ')
  })
})
