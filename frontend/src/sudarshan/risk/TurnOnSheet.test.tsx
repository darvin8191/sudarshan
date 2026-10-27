/**
 * Turning the kill switch on (M20.7, amendments R1 to R3): what will happen counted fresh, Exit
 * all unticked, the phrase before anything is sent, a half second in which a tap does nothing,
 * one send however it is tapped, and the rows it acts on held from the tap.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SwitchAnswer } from '@/api/risk'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { cancelMark, exitMark, resetSentForTests, sentMarksNow } from '@/sudarshan/books/sent'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'

const api = vi.hoisted(() => ({ preview: vi.fn(), turnOn: vi.fn() }))
const trading = vi.hoisted(() => ({ getPositions: vi.fn(), getOrders: vi.fn() }))
vi.mock('@/api/risk', () => ({ riskApi: api }))
vi.mock('@/api/trading', () => ({ tradingApi: trading }))

import { TurnOnSheet } from './TurnOnSheet'

const PREVIEW = {
  counts: { orders: 2, gtts: 1, stops: 1, protected: 1, positions: 1 },
  message:
    '2 open orders and 1 GTT will be cancelled, including 1 stop-loss order that protects 1 position. That position stays open without a stop unless you also tick Exit all.',
}
const ON_ANSWER: SwitchAnswer = {
  code: 200,
  ok: true,
  message: 'Kill switch on: new orders are refused; exits and cancels still go.',
  activated: true,
  state: {
    known: true,
    on: true,
    report: {
      activation_id: 'ks-1',
      steps: [
        { step: 'saved', ok: true, words: 'Saved: it stays on if the app restarts.' },
        { step: 'live_orders', ok: true, words: '2 open orders cancelled.' },
      ],
    },
  },
}

function renderSheet(stage: 'on' | 'sandbox' | 'off' = 'on', onOpenChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <TurnOnSheet open onOpenChange={onOpenChange} stage={stage} />
    </QueryClientProvider>
  )
  return onOpenChange
}

const phraseField = () => screen.getByLabelText('Confirm phrase')
const turnOn = () => screen.getByRole('button', { name: 'Turn kill switch on' })

beforeEach(() => {
  vi.clearAllMocks()
  resetSentForTests()
  useAuthStore.setState({ apiKey: 'fake-key' })
  useThemeStore.setState({ appMode: 'live' })
  api.preview.mockResolvedValue(PREVIEW)
  api.turnOn.mockResolvedValue(ON_ANSWER)
  trading.getPositions.mockResolvedValue({
    status: 'success',
    data: [{ symbol: 'NIFTY27OCT2625900CE', exchange: 'NFO', product: 'MIS', quantity: 65 }],
  })
  trading.getOrders.mockResolvedValue({
    status: 'success',
    data: { orders: [{ orderid: 'A1', order_status: 'trigger pending' }], statistics: {} },
  })
})
afterEach(() => {
  OPEN_GUARD.ms = 0
  vi.useRealTimers()
})

describe('turning the kill switch on', () => {
  it('says what will be cancelled, counted fresh, before anything is sent', async () => {
    renderSheet()
    expect(await screen.findByTestId('kill-switch-preview')).toHaveTextContent(PREVIEW.message)
    expect(api.turnOn).not.toHaveBeenCalled()
  })

  it('asks for the phrase first, and sends Exit all only when ticked', async () => {
    renderSheet()
    await screen.findByTestId('kill-switch-preview')
    expect(turnOn()).toBeDisabled()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    fireEvent.change(phraseField(), { target: { value: 'stop' } })
    expect(turnOn()).toBeEnabled()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(turnOn())
    await waitFor(() => expect(api.turnOn).toHaveBeenCalledWith(true, 'stop'))
  })

  it('sends once however it is tapped', async () => {
    let answer: (value: SwitchAnswer) => void = () => {}
    api.turnOn.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    renderSheet()
    await screen.findByTestId('kill-switch-preview')
    fireEvent.change(phraseField(), { target: { value: 'STOP' } })
    const button = turnOn()
    fireEvent.click(button)
    fireEvent.click(button)
    fireEvent.submit(button.closest('form') as HTMLFormElement)
    await act(async () => answer(ON_ANSWER))
    expect(api.turnOn).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('2 open orders cancelled.')).toBeInTheDocument()
  })

  it('ignores taps for half a second after it opens', async () => {
    OPEN_GUARD.ms = 500
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderSheet()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    await act(async () => {
      vi.advanceTimersByTime(600)
    })
    fireEvent.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('holds the rows it acts on from the tap, and lets them go on a refusal', async () => {
    let answer: (value: SwitchAnswer) => void = () => {}
    api.turnOn.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    renderSheet()
    await screen.findByTestId('kill-switch-preview')
    await waitFor(() => expect(trading.getOrders).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.change(phraseField(), { target: { value: 'go' } })
    // The book read when the sheet opened is what is held.
    await waitFor(() => expect(trading.getPositions).toHaveBeenCalledWith('fake-key'))
    fireEvent.click(turnOn())
    await waitFor(() =>
      expect(exitMark(sentMarksNow(), false, 'NFO:NIFTY27OCT2625900CE:MIS')?.state).toBe('sending')
    )
    expect(cancelMark(sentMarksNow(), false, 'A1')?.state).toBe('sending')
    await act(async () =>
      answer({
        code: 400,
        ok: false,
        message: 'The confirm phrase does not match. Nothing was changed.',
        state: null,
        activated: false,
      })
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('The confirm phrase does not match')
    expect(sentMarksNow().size).toBe(0)
    expect(phraseField()).toHaveValue('')
  })

  it('never reads or holds the Live book while the guard acts on the sandbox only', async () => {
    renderSheet('sandbox')
    expect(await screen.findByText(/The sandbox only/)).toBeInTheDocument()
    fireEvent.change(phraseField(), { target: { value: 'STOP' } })
    fireEvent.click(turnOn())
    await waitFor(() => expect(api.turnOn).toHaveBeenCalled())
    expect(trading.getPositions).not.toHaveBeenCalled()
    expect(sentMarksNow().size).toBe(0)
  })

  it('says so when no answer came back, and keeps the rows held', async () => {
    api.turnOn.mockRejectedValue(new Error('timeout'))
    renderSheet()
    await screen.findByTestId('kill-switch-preview')
    await waitFor(() => expect(trading.getOrders).toHaveBeenCalled())
    fireEvent.change(phraseField(), { target: { value: 'STOP' } })
    fireEvent.click(turnOn())
    expect(await screen.findByRole('alert')).toHaveTextContent('No answer came back')
    expect(cancelMark(sentMarksNow(), false, 'A1')?.state).toBe('unconfirmed')
  })
})
