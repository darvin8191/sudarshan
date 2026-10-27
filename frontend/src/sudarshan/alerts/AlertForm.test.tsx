/**
 * The alert form (M10.3): a new alert opens on the contract's defaults with its level from the
 * snapped live price, each kind brings its own defaults, choices that make no sense for an index
 * are off with the reason, the level is read back as the engine reads it, a crossing that would
 * wait says so, an edit opens on the alert's own settings and sends them all back (its end only
 * when changed, its version as it opened), Save sends once, and a refusal marks its field in the
 * server's words. The instrument search is a stand-in; its real dialog is /trading's.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createElement } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const api = vi.hoisted(() => ({ create: vi.fn(), change: vi.fn() }))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: api,
}))

const toasts = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ toast: toasts, showToast: toasts }))

vi.mock('@/components/trading/SymbolSearchDialog', () => ({
  SymbolSearchDialog: (props: { open: boolean; onPick: (row: Record<string, unknown>) => void }) =>
    props.open
      ? createElement(
          'section',
          { 'aria-label': 'Instrument search' },
          createElement(
            'button',
            {
              type: 'button',
              onClick: () => props.onPick({ symbol: 'SBIN', exchange: 'NSE', tick_size: 0.05 }),
            },
            'SBIN on NSE'
          ),
          createElement(
            'button',
            { type: 'button', onClick: () => props.onPick({ symbol: 'GOLDM', exchange: 'MCX' }) },
            'GOLDM on MCX'
          )
        )
      : null,
}))

import { type AlertRow, AlertsError } from '@/api/marketAlerts'
import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { alertRow, alertsState } from '@/test/marketAlerts'
import { feed } from '@/test/marketDataHarness'
import { AlertFormSheet, type AlertPrefill } from './AlertForm'
import { STATE_KEY } from './stateCache'

const NIFTY: AlertPrefill = {
  symbol: 'NIFTY',
  exchange: 'NSE_INDEX',
  tick: null,
  price: 25903.149999999998,
}
const SBIN: AlertPrefill = { symbol: 'SBIN', exchange: 'NSE', tick: 0.05, price: 99.95 }

function renderForm(props: Partial<Parameters<typeof AlertFormSheet>[0]> = {}) {
  const client = createQueryClient()
  client.setQueryData(STATE_KEY, alertsState())
  const onSaved = vi.fn()
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AlertFormSheet onSaved={onSaved} onClose={onClose} {...props} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return { client, onSaved, onClose, sheet: screen.getByRole('dialog') }
}

const pressed = (sheet: HTMLElement, legend: string) =>
  within(within(sheet).getByRole('group', { name: legend }))
    .getAllByRole('button')
    .filter((button) => button.getAttribute('aria-pressed') === 'true')
    .map((button) => button.textContent)

beforeEach(() => {
  feed.reset()
  api.create.mockReset()
  api.change.mockReset()
  for (const fn of Object.values(toasts)) fn.mockReset()
  useAuthStore.setState({ apiKey: 'key' })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('a new alert', () => {
  it('opens on the contract’s defaults, its level the live price snapped to the paisa', () => {
    const { sheet } = renderForm({ prefill: NIFTY })

    expect(within(sheet).getByRole('heading', { name: 'New alert' })).toBeInTheDocument()
    expect(within(sheet).getByLabelText('Price level')).toHaveValue('25903.15')
    expect(pressed(sheet, 'Alert when')).toEqual(['Price'])
    expect(within(sheet).getByLabelText('Check')).toHaveValue('crosses_above')
    expect(pressed(sheet, 'How often')).toEqual(['Once'])
    expect(within(sheet).getByLabelText('Wait between alerts')).toHaveValue('0')
    expect(pressed(sheet, 'Ends')).toEqual(['In 60 days'])
    expect(within(sheet).getByText('As in your alert settings.')).toBeInTheDocument()
  })

  it('runs a derivative to its expiry unless told otherwise', () => {
    const { sheet } = renderForm({
      prefill: { symbol: 'NIFTY25NOV26FUT', exchange: 'NFO', tick: 0.1, price: 26010.1 },
    })
    expect(pressed(sheet, 'Ends')).toEqual(['At expiry'])
  })

  it('reads the level back as the engine will, and warns when a crossing would wait', async () => {
    const user = userEvent.setup()
    const { sheet } = renderForm({ prefill: NIFTY })

    await user.clear(within(sheet).getByLabelText('Price level'))
    await user.type(within(sheet).getByLabelText('Price level'), '25900')
    expect(within(sheet).getByText('Checks at 25,900.00')).toBeInTheDocument()
    expect(
      within(sheet).getByText(
        "NIFTY is already at or above 25,900.00. 'Crosses above' waits for it to fall below and come back; 'Is at or above' fires at once."
      )
    ).toBeInTheDocument()

    await user.selectOptions(within(sheet).getByLabelText('Check'), 'at_or_above')
    expect(
      within(sheet).getByText('NIFTY is at or above 25,900.00 now, so this fires at once.')
    ).toBeInTheDocument()
  })

  it('says where a level off the tick is met', async () => {
    const user = userEvent.setup()
    const { sheet } = renderForm({ prefill: SBIN })

    await user.clear(within(sheet).getByLabelText('Price level'))
    await user.type(within(sheet).getByLabelText('Price level'), '100.03')
    expect(within(sheet).getByText('Checks at ₹100.05 (nearest tick)')).toBeInTheDocument()
  })

  it('gives each kind its own defaults', async () => {
    const user = userEvent.setup()
    const { sheet } = renderForm({ prefill: SBIN })

    await user.click(within(sheet).getByRole('button', { name: '% move' }))
    expect(pressed(sheet, 'How often')).toEqual(['Once per candle'])
    expect(pressed(sheet, 'Candle')).toEqual(['Day'])
    expect(pressed(sheet, 'From')).toEqual(['Previous close'])

    await user.click(within(sheet).getByRole('button', { name: 'Day high/low' }))
    expect(pressed(sheet, 'How often')).toEqual(['Once per candle'])
    expect(within(sheet).getByLabelText('From')).toHaveValue('09:30')
    expect(within(sheet).getByRole('button', { name: 'At candle close' })).toBeDisabled()

    await user.click(within(sheet).getByRole('button', { name: 'VWAP' }))
    expect(pressed(sheet, 'How often')).toEqual(['Every time'])
    expect(within(sheet).getByLabelText('Wait between alerts')).toHaveValue('900')
    expect(within(sheet).queryByRole('button', { name: 'Volume' })).toBeNull()
  })

  it('turns off what an index cannot have, and says why', () => {
    const { sheet } = renderForm({ prefill: NIFTY })

    expect(within(sheet).getByRole('button', { name: 'VWAP' })).toBeDisabled()
    expect(
      within(sheet).getByText('An index has no traded volume, so it has no VWAP. Use its future.')
    ).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Bid' })).toBeDisabled()
    expect(
      within(sheet).getByText('An index has no bid or ask. Watch its last price instead.')
    ).toBeInTheDocument()
  })

  it('refuses an instrument market alerts do not cover, in the server’s sentence', async () => {
    const user = userEvent.setup()
    const { sheet } = renderForm()

    await user.click(within(sheet).getByRole('button', { name: /Pick an instrument/ }))
    await user.click(screen.getByRole('button', { name: 'GOLDM on MCX' }))
    expect(
      within(sheet).getByText(
        'Market alerts work on NSE, BSE, NFO and BFO instruments and their indices for now.'
      )
    ).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Save' })).toBeDisabled()

    await user.click(within(sheet).getByRole('button', { name: /Pick an instrument/ }))
    await user.click(screen.getByRole('button', { name: 'SBIN on NSE' }))
    expect(within(sheet).getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it('saves once however fast it is tapped, sending the draft without an end of its own', async () => {
    let answer: (row: AlertRow) => void = () => {}
    api.create.mockReturnValue(
      new Promise<AlertRow>((resolve) => {
        answer = resolve
      })
    )
    const user = userEvent.setup()
    const { sheet, onSaved, client } = renderForm({ prefill: SBIN })

    await user.dblClick(within(sheet).getByRole('button', { name: 'Save' }))
    expect(api.create).toHaveBeenCalledTimes(1)
    expect(api.create.mock.calls[0][0]).toEqual({
      symbol: 'SBIN',
      exchange: 'NSE',
      kind: 'price',
      params: { op: 'crosses_above', level: '99.95', source: 'ltp' },
      frequency: 'once',
      cooldown_s: 0,
      note: '',
      channels: null,
      price: 99.95,
    })

    const saved = alertRow({ id: 77 })
    answer(saved)
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved, true))
    expect(
      client.getQueryData<{ alerts: AlertRow[] }>(STATE_KEY)?.alerts.map((row) => row.id)
    ).toContain(77)
  })

  it('marks the field the answer names, in the server’s words, and lets Save go again', async () => {
    const sentence = "Every time on 'Is at or above' needs at least 1 minute between alerts."
    api.create.mockRejectedValue(
      new AlertsError({ code: 'invalid', message: sentence, field: 'cooldown_s' })
    )
    const user = userEvent.setup()
    const { sheet } = renderForm({ prefill: SBIN })

    await user.click(within(sheet).getByRole('button', { name: 'Save' }))

    const wait = within(sheet).getByLabelText('Wait between alerts')
    expect(wait).toHaveAttribute('aria-invalid', 'true')
    expect(document.getElementById(wait.getAttribute('aria-describedby') ?? '')?.textContent).toBe(
      sentence
    )
    expect(within(sheet).getByRole('button', { name: 'Save' })).toBeEnabled()

    await user.selectOptions(wait, '60')
    expect(within(sheet).queryByText(sentence)).toBeNull()
  })
})

describe('an edit', () => {
  const held = alertRow({
    params: { op: 'at_or_above', level: '100', source: 'ltp' },
    frequency: 'every_time',
    cooldown_s: 300,
    note: 'book half',
    channels: { telegram: true, app: false },
  })

  it('opens on the alert’s own settings and sends them all back, its end only when changed', async () => {
    api.change.mockResolvedValue({ ...held, version: 4 })
    const user = userEvent.setup()
    const { sheet, onSaved } = renderForm({ row: held })

    expect(within(sheet).getByRole('heading', { name: 'Edit alert' })).toBeInTheDocument()
    expect(within(sheet).getByLabelText('Check')).toHaveValue('at_or_above')
    expect(pressed(sheet, 'How often')).toEqual(['Every time'])
    expect(within(sheet).getByLabelText('Wait between alerts')).toHaveValue('300')
    expect(pressed(sheet, 'Ends')).toEqual(['As now'])
    expect(within(sheet).getByRole('switch', { name: 'App' })).not.toBeChecked()

    await user.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(api.change).toHaveBeenCalledWith(12, {
      version: 3,
      action: 'edit',
      alert: {
        symbol: 'SBIN',
        exchange: 'NSE',
        kind: 'price',
        params: { op: 'at_or_above', level: '100', source: 'ltp' },
        frequency: 'every_time',
        cooldown_s: 300,
        note: 'book half',
        channels: { telegram: true, app: false },
      },
    })
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ ...held, version: 4 }, false))
  })

  it('sends no end, a date or null as the owner picks', async () => {
    api.change.mockResolvedValue(held)
    const user = userEvent.setup()
    const { sheet } = renderForm({ row: held })

    await user.click(within(sheet).getByRole('button', { name: 'No end' }))
    await user.click(within(sheet).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.change).toHaveBeenCalledTimes(1))
    expect(api.change.mock.calls[0][1].alert.ends).toBeNull()
  })

  it('sends the version it opened on, so an edit made elsewhere meanwhile is refused', async () => {
    const sentence = 'This alert was changed on another screen. Open it again, then edit it.'
    api.change.mockRejectedValue(
      new AlertsError({ code: 'conflict', message: sentence, field: null })
    )
    const user = userEvent.setup()
    const { sheet, client } = renderForm({ row: held })
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    await user.click(within(sheet).getByRole('button', { name: 'Save' }))

    expect(api.change.mock.calls[0][1].version).toBe(3)
    expect(within(sheet).getByRole('alert')).toHaveTextContent(sentence)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: STATE_KEY })
  })
})
