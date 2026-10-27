/**
 * Alert settings (M10.14): Telegram and App on or off, read from the server and saved there for
 * every alert and every device (each change sent once, a refusal in the server's words and the
 * switches back to the server's values, the values as last read while a read fails); Send a
 * test, sent once, with how each channel delivered
 * it, a delivery still sending settling by push; and the way to this device's sound switch.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ prefs: vi.fn(), setPrefs: vi.fn(), test: vi.fn() }))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: api,
}))

import { AlertsError, type MarketAlertsState, type Prefs } from '@/api/marketAlerts'
import { createQueryClient } from '@/app/providers'
import { alertsState, NOW } from '@/test/marketAlerts'
import {
  bindMarketAlertsCache,
  installMarketAlerts,
  resetMarketAlertsForTests,
} from './marketAlerts'
import { SettingsSheet } from './SettingsSheet'
import { STATE_KEY } from './stateCache'

function renderSheet(state: MarketAlertsState = alertsState()) {
  const client = createQueryClient()
  client.setQueryData(STATE_KEY, state)
  bindMarketAlertsCache(client)
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SettingsSheet onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return { client, sheet: screen.getByRole('dialog', { name: 'Alert settings' }) }
}

const chips = (scope: HTMLElement) =>
  [...scope.querySelectorAll('[data-delivery]')].map((chip) => chip.textContent)

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  resetMarketAlertsForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  api.prefs.mockResolvedValue({ telegram: true, app: true })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('where alerts go', () => {
  it('reads the settings from the server and saves a change there, once', async () => {
    let answer: (prefs: Prefs) => void = () => {}
    api.setPrefs.mockReturnValue(
      new Promise<Prefs>((resolve) => {
        answer = resolve
      })
    )
    const user = userEvent.setup()
    const { client, sheet } = renderSheet()
    const app = within(sheet).getByRole('switch', { name: 'App' })
    await waitFor(() => expect(app).toBeChecked())
    expect(within(sheet).getByRole('switch', { name: 'Telegram' })).toBeChecked()

    await user.click(app)
    expect(api.setPrefs).toHaveBeenCalledWith({ telegram: true, app: false })
    expect(app).toBeDisabled()
    await user.click(app)
    expect(api.setPrefs).toHaveBeenCalledTimes(1)

    await act(async () => answer({ telegram: true, app: false }))
    await waitFor(() => expect(app).not.toBeChecked())
    expect(client.getQueryData<MarketAlertsState>(STATE_KEY)?.prefs).toEqual({
      telegram: true,
      app: false,
    })
  })

  it('shows a refusal in the server’s words, and the switches as the server has them', async () => {
    api.setPrefs.mockRejectedValue(
      new AlertsError({
        code: 'off',
        message: 'Market alerts are switched off on the server.',
        field: null,
      })
    )
    const user = userEvent.setup()
    const { sheet } = renderSheet()
    const telegram = within(sheet).getByRole('switch', { name: 'Telegram' })
    await waitFor(() => expect(telegram).toBeChecked())

    await user.click(telegram)
    expect(await within(sheet).findByRole('alert')).toHaveTextContent(
      'Market alerts are switched off on the server.'
    )
    expect(telegram).toBeChecked()
    expect(api.prefs).toHaveBeenCalledTimes(2)
  })

  it('shows the settings as last read, and takes no change, while they cannot be read', async () => {
    const sentence =
      'The alert service is not answering. Your alerts are kept; checking resumes when it is back.'
    api.prefs.mockRejectedValue(
      new AlertsError({ code: 'not_answering', message: sentence, field: null })
    )
    const { sheet } = renderSheet(alertsState({ prefs: { telegram: true, app: false } }))

    expect(await within(sheet).findByRole('alert')).toHaveTextContent(sentence)
    const telegram = within(sheet).getByRole('switch', { name: 'Telegram' })
    expect(telegram).toBeChecked()
    expect(telegram).toBeDisabled()
    expect(within(sheet).getByRole('switch', { name: 'App' })).not.toBeChecked()
    expect(api.setPrefs).not.toHaveBeenCalled()
  })
})

describe('the test', () => {
  it('sends once, shows each channel’s result, and a delivery still sending settles by push', async () => {
    api.test.mockResolvedValue({
      event_id: 400,
      deliveries: {
        telegram: { status: 'pending', reason: '', attempts: 1, updated_at: NOW },
        app: { status: 'sent', reason: 'Shown on 1 open screen', attempts: 1, updated_at: NOW },
      },
    })
    const handlers = new Map<string, (...args: unknown[]) => void>()
    const socket = { connected: false, listeners: () => [] }
    installMarketAlerts(socket as never, (event, handler) => handlers.set(event, handler))
    const user = userEvent.setup()
    const { sheet } = renderSheet()

    await user.dblClick(within(sheet).getByRole('button', { name: 'Send a test' }))
    await waitFor(() => expect(chips(sheet)).toEqual(['Telegram · Sending', 'App · Sent 10:42:31']))
    expect(api.test).toHaveBeenCalledTimes(1)
    expect(within(sheet).getByText('Shown on 1 open screen')).toBeInTheDocument()

    act(() =>
      handlers.get('sudarshan_market_alert_delivery')?.({
        event_id: 400,
        channel: 'telegram',
        delivery: { status: 'sent', reason: '', attempts: 1, updated_at: NOW + 2_000 },
      })
    )
    await waitFor(() =>
      expect(chips(sheet)).toEqual(['Telegram · Sent 10:42:33', 'App · Sent 10:42:31'])
    )
  })

  it('shows why a test could not be sent, in the server’s words', async () => {
    const sentence = 'Too many requests. Wait a minute, then try again.'
    api.test.mockRejectedValue(new AlertsError({ code: 'busy', message: sentence, field: null }))
    const user = userEvent.setup()
    const { sheet } = renderSheet()

    await user.click(within(sheet).getByRole('button', { name: 'Send a test' }))
    expect(await within(sheet).findByRole('alert')).toHaveTextContent(sentence)
    expect(within(sheet).getByRole('button', { name: 'Send a test' })).toBeEnabled()
  })
})

it('leads to the sound switch of this device, in Profile > Display', () => {
  const { sheet } = renderSheet()
  expect(
    within(sheet).getByRole('link', {
      name: 'Market alert sound on this device, in Profile > Display',
    })
  ).toHaveAttribute('href', '/profile?tab=theme')
})
