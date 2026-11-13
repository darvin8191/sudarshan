/**
 * The list mode of the alert form (M8.14): "Every symbol of Swing (24)", only the three kinds that
 * make sense across many symbols, the two deliveries, the right body to POST .../groups, and the
 * service's own sentences after Save (fits, does not fit, left out, a ranked list) shown as sent.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ createGroup: vi.fn(), changeGroup: vi.fn(), state: vi.fn() }))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: api,
}))
vi.mock('@/utils/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.setConfig({ testTimeout: 15_000 })

import { AlertsError } from '@/api/marketAlerts'
import { createQueryClient } from '@/app/providers'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import { alertsState, NOW } from '@/test/marketAlerts'
import { everySymbol, FED_HINT, ListAlertFormSheet, type ListTarget } from './ListAlertForm'
import { STATE_KEY } from './stateCache'
import { groupRow } from './testGroups'

const SWING: ListTarget = { ref: 'list:swing', name: 'Swing', count: 24, fed: false }

function renderForm(props: Parameters<typeof ListAlertFormSheet>[0]) {
  const client = createQueryClient()
  client.setQueryData(STATE_KEY, alertsState())
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ListAlertFormSheet {...props} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return { client, onClose, sheet: screen.getByRole('dialog') }
}

const pressed = (sheet: HTMLElement, legend: string) =>
  within(within(sheet).getByRole('group', { name: legend }))
    .getAllByRole('button')
    .filter((button) => button.getAttribute('aria-pressed') === 'true')
    .map((button) => button.textContent)

const labels = (sheet: HTMLElement, legend: string) =>
  within(within(sheet).getByRole('group', { name: legend }))
    .getAllByRole('button')
    .map((button) => button.textContent)

const FITS =
  'This alert needs 24 instruments and 24 alerts. In use now: 150 of 200 instruments and 310 of 500 Active alerts. It fits.'

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset()
})

describe('the form in list mode', () => {
  it('names the list and its rows, and offers three kinds and two deliveries', () => {
    const { sheet } = renderForm({ target: SWING })
    expect(within(sheet).getByText('Every symbol of Swing (24)')).toBeInTheDocument()
    expect(labels(sheet, 'Alert when')).toEqual(['% move', 'Day high/low', 'VWAP'])
    expect(within(sheet).queryByText('Price')).toBeNull()
    expect(labels(sheet, 'Telegram')).toEqual([
      'Alerts that follow within 5 minutes go as one message',
      'Each alert at once',
    ])
    expect(pressed(sheet, 'Telegram')).toEqual([
      'Alerts that follow within 5 minutes go as one message',
    ])
    expect(within(sheet).queryByText(FED_HINT)).toBeNull()
    expect(everySymbol('', null)).toBe('Every symbol of this list')
    expect(everySymbol('Nifty 50', 1234)).toBe('Every symbol of Nifty 50 (1,234)')
  })

  it('says a screen feeds the list', () => {
    const { sheet } = renderForm({ target: { ...SWING, fed: true } })
    expect(within(sheet).getByText(FED_HINT)).toBeInTheDocument()
  })

  it('sends the draft to the group route and shows the fit sentence as it came', async () => {
    api.createGroup.mockResolvedValue({
      group: groupRow({
        left_out: ['ATM and strike rows change contract during the day and are left out: 2.'],
      }),
      fit: FITS,
    })
    const user = userEvent.setup()
    const { sheet, client } = renderForm({ target: SWING })
    await user.clear(within(sheet).getByLabelText('Move, in %'))
    await user.type(within(sheet).getByLabelText('Move, in %'), '2')
    await user.click(within(sheet).getByRole('button', { name: 'Each alert at once' }))
    await user.click(within(sheet).getByRole('button', { name: 'Save' }))

    expect(api.createGroup).toHaveBeenCalledTimes(1)
    expect(api.createGroup).toHaveBeenCalledWith({
      ref: 'list:swing',
      name: 'Swing',
      kind: 'move',
      params: { base: 'close', direction: 'up', percent: '2' },
      frequency: 'per_candle',
      candle: 'day',
      cooldown_s: 0,
      note: '',
      delivery: 'each',
      digest_min: 5,
    })
    expect(await within(sheet).findByText(FITS)).toBeInTheDocument()
    expect(
      within(sheet).getByText(
        'ATM and strike rows change contract during the day and are left out: 2.'
      )
    ).toBeInTheDocument()
    expect(client.getQueryData<{ groups: unknown[] }>(STATE_KEY)?.groups).toHaveLength(1)
    await user.click(within(sheet).getByRole('button', { name: 'Done' }))
  })

  it('sends the default delivery of the first alert at once, then one message', async () => {
    api.createGroup.mockResolvedValue({ group: groupRow(), fit: FITS })
    const user = userEvent.setup()
    const { sheet } = renderForm({ target: SWING })
    await user.click(within(sheet).getByRole('button', { name: 'Day high/low' }))
    await user.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(api.createGroup.mock.calls[0]?.[0]).toMatchObject({
      kind: 'day_break',
      params: { side: 'high', after: '09:30' },
      delivery: 'first_then_digest',
    })
  })

  it('sends once however often Save is tapped', async () => {
    let answer: (value: unknown) => void = () => {}
    api.createGroup.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    const user = userEvent.setup()
    const { sheet } = renderForm({ target: SWING })
    const save = within(sheet).getByRole('button', { name: 'Save' })
    await user.click(save)
    await user.click(save)
    expect(api.createGroup).toHaveBeenCalledTimes(1)
    answer({ group: groupRow(), fit: FITS })
    expect(await within(sheet).findByText(FITS)).toBeInTheDocument()
  })

  it('shows the refusal with its numbers as sent, and lets the owner go back to the form', async () => {
    const refusal =
      'It does not fit: it needs 180 alerts and 140 of the 500 are free. Nothing was made.'
    api.createGroup.mockRejectedValue(
      new AlertsError({ code: 'limit', message: refusal, field: null })
    )
    const user = userEvent.setup()
    const { sheet } = renderForm({ target: SWING })
    await user.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(await within(sheet).findByText(refusal)).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Save' })).toBeEnabled()
    expect(within(sheet).queryByText('List alert set')).toBeNull()
  })

  it('shows a ranked list refusal as sent', async () => {
    const words =
      'A ranked list changes with every read. Set the alert on a list or a built-in list instead.'
    api.createGroup.mockRejectedValue(
      new AlertsError({ code: 'invalid', message: words, field: 'ref' })
    )
    const user = userEvent.setup()
    const { sheet } = renderForm({ target: { ...SWING, ref: 'ranked:gainers' } })
    await user.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(await within(sheet).findByText(words)).toBeInTheDocument()
  })

  it('refuses a percent that is not plain digits before sending', async () => {
    const user = userEvent.setup()
    const { sheet } = renderForm({ target: SWING })
    await user.type(within(sheet).getByLabelText('Move, in %'), 'e5')
    await user.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(api.createGroup).not.toHaveBeenCalled()
  })
})

describe('editing a list alert', () => {
  it('opens on its own settings, keeps its kind, and sends its version with the edit', async () => {
    api.changeGroup.mockResolvedValue({ group: groupRow({ version: 3 }), fit: '' })
    const user = userEvent.setup()
    const { sheet } = renderForm({ group: groupRow() })
    expect(within(sheet).getByText('Every symbol of Swing (24)')).toBeInTheDocument()
    expect(pressed(sheet, 'Alert when')).toEqual(['% move'])
    expect(within(sheet).getByRole('button', { name: 'Day high/low' })).toBeDisabled()
    expect(within(sheet).getByLabelText('Move, in %')).toHaveValue('2')
    await user.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(api.changeGroup).toHaveBeenCalledWith(7, {
      version: 2,
      action: 'edit',
      group: expect.objectContaining({
        name: 'Swing',
        kind: 'move',
        delivery: 'first_then_digest',
      }),
    })
    expect(await within(sheet).findByText('List alert saved')).toBeInTheDocument()
  })

  it('shows a stale version in the service’s words', async () => {
    const words = 'This alert was changed on another screen. Your alerts are read again.'
    api.changeGroup.mockRejectedValue(
      new AlertsError({ code: 'conflict', message: words, field: null })
    )
    const user = userEvent.setup()
    const { sheet } = renderForm({ group: groupRow() })
    await user.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(await within(sheet).findByText(words)).toBeInTheDocument()
  })
})

describe('the half second after it opens', () => {
  it('takes no tap on Save until it has passed', async () => {
    OPEN_GUARD.ms = 500
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
    try {
      api.createGroup.mockResolvedValue({ group: groupRow(), fit: FITS })
      const user = userEvent.setup()
      const { sheet } = renderForm({ target: SWING })
      await user.click(within(sheet).getByRole('button', { name: 'Save' }))
      expect(api.createGroup).not.toHaveBeenCalled()
      vi.setSystemTime(NOW + 600)
      await user.click(within(sheet).getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(api.createGroup).toHaveBeenCalledTimes(1))
    } finally {
      vi.useRealTimers()
      OPEN_GUARD.ms = 0
    }
  })
})
