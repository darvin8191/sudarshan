/**
 * History (M10.4): each fire with its time in India, the server's frozen words and value, its
 * marks, and one delivery chip per channel (an icon and a word, the time once sent, the server's
 * reason under it); older pages a page at a time by `before`; an alert's last five fires in its
 * sheet; a failed read in the server's words.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ events: vi.fn() }))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: api,
}))

import { AlertsError } from '@/api/marketAlerts'
import { createQueryClient } from '@/app/providers'
import { alertEvent, NOW } from '@/test/marketAlerts'
import { HistoryList, RecentFires } from './HistoryList'
import { resetMarketAlertsForTests } from './marketAlerts'

function renderIn(node: React.ReactNode) {
  render(<QueryClientProvider client={createQueryClient()}>{node}</QueryClientProvider>)
}

const item = (id: number) => document.querySelector(`[data-event="${id}"]`) as HTMLElement

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  resetMarketAlertsForTests()
  api.events.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('History', () => {
  it('shows each fire with its time, the server’s words and value, and its marks', async () => {
    api.events.mockResolvedValue({
      events: [
        alertEvent({
          id: 343,
          symbol: 'NIFTY',
          exchange: 'NSE_INDEX',
          title: 'NIFTY opened above 25,900.00',
          detail: 'first price 25,932.10 (previous 25,861.40)',
          value: '25,932.10',
          level: '25,900.00',
          opening: true,
          note: 'book half',
        }),
        alertEvent({ id: 342, found_later: true }),
        alertEvent({
          id: 341,
          alert_id: 0,
          symbol: '',
          exchange: '',
          title: 'Test alert',
          detail: 'sent from the Alerts page',
          test: true,
          at: NOW - 86_400_000,
        }),
      ],
      more: false,
    })
    renderIn(<HistoryList now={NOW} />)
    await waitFor(() => expect(item(343)).not.toBeNull())

    expect(within(item(343)).getByText('NIFTY opened above 25,900.00')).toBeInTheDocument()
    expect(
      within(item(343)).getByText('first price 25,932.10 (previous 25,861.40) · Note: book half')
    ).toBeInTheDocument()
    expect(within(item(343)).getByText('NIFTY · NSE INDEX')).toBeInTheDocument()
    expect(within(item(343)).getByText('10:37:31')).toBeInTheDocument()
    expect(item(343).querySelector('[data-mark="Opened above"] svg')).not.toBeNull()
    expect(item(342).querySelector('[data-mark="Seen after a pause"] svg')).not.toBeNull()
    expect(item(341).querySelector('[data-mark="Test"] svg')).not.toBeNull()
    expect(within(item(341)).getByText('27 Oct 10:42:31')).toBeInTheDocument()
  })

  it('gives each channel a chip with its word and icon, the time once sent, and the reason', async () => {
    api.events.mockResolvedValue({
      events: [
        alertEvent({
          id: 341,
          deliveries: {
            telegram: {
              status: 'not_sent',
              reason: 'Telegram did not confirm it after 3 tries',
              attempts: 3,
              updated_at: NOW,
            },
            app: { status: 'not_sent', reason: 'No app was open', attempts: 1, updated_at: NOW },
          },
        }),
        alertEvent({ id: 340 }),
        alertEvent({
          id: 339,
          deliveries: {
            telegram: {
              status: 'off',
              reason: 'Off in your alert settings',
              attempts: 0,
              updated_at: NOW,
            },
            app: { status: 'pending', reason: '', attempts: 0, updated_at: NOW },
          },
        }),
      ],
      more: false,
    })
    renderIn(<HistoryList now={NOW} />)
    await waitFor(() => expect(item(341)).not.toBeNull())

    const chips = (id: number) =>
      [...item(id).querySelectorAll('[data-delivery]')].map((chip) => {
        expect(chip.querySelector('svg')).not.toBeNull()
        return chip.textContent
      })
    expect(chips(341)).toEqual(['Telegram · Not sent', 'App · Not sent'])
    expect(
      within(item(341)).getByText('Telegram did not confirm it after 3 tries')
    ).toBeInTheDocument()
    expect(within(item(341)).getByText('No app was open')).toBeInTheDocument()
    expect(chips(340)).toEqual(['Telegram · Sent 10:37:32', 'App · Sent 10:37:31'])
    expect(within(item(340)).getByText('Shown on 1 open screen')).toBeInTheDocument()
    expect(chips(339)).toEqual(['Telegram · Off', 'App · Sending'])
    expect(within(item(339)).getByText('Off in your alert settings')).toBeInTheDocument()
  })

  it('reads older pages by the last id it holds, while the server says there are more', async () => {
    api.events
      .mockResolvedValueOnce({
        events: [alertEvent({ id: 345 }), alertEvent({ id: 344 })],
        more: true,
      })
      .mockResolvedValueOnce({ events: [alertEvent({ id: 300 })], more: false })
    const user = userEvent.setup()
    renderIn(<HistoryList now={NOW} />)
    await waitFor(() => expect(item(344)).not.toBeNull())
    expect(api.events).toHaveBeenLastCalledWith({ before: null, limit: 50 })

    await user.click(screen.getByRole('button', { name: 'Show older' }))
    await waitFor(() => expect(item(300)).not.toBeNull())
    expect(api.events).toHaveBeenLastCalledWith({ before: 344, limit: 50 })
    expect(screen.queryByRole('button', { name: 'Show older' })).toBeNull()
  })

  it('says so when nothing has fired', async () => {
    api.events.mockResolvedValue({ events: [], more: false })
    renderIn(<HistoryList now={NOW} />)
    expect(await screen.findByText('Nothing has fired yet')).toBeInTheDocument()
  })

  it('shows a failed read in the server’s words, with Try again', async () => {
    const sentence =
      'The alert service is not answering. Your alerts are kept; checking resumes when it is back.'
    api.events
      .mockRejectedValueOnce(
        new AlertsError({ code: 'not_answering', message: sentence, field: null })
      )
      .mockResolvedValue({ events: [alertEvent()], more: false })
    const user = userEvent.setup()
    renderIn(<HistoryList now={NOW} />)

    expect(await screen.findByText(sentence)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(item(341)).not.toBeNull())
  })
})

describe('an alert’s last fires', () => {
  it('reads its own five, newest first', async () => {
    api.events.mockResolvedValue({ events: [alertEvent({ id: 345 })], more: true })
    renderIn(<RecentFires alertId={12} now={NOW} />)

    await waitFor(() => expect(item(345)).not.toBeNull())
    expect(api.events).toHaveBeenCalledWith({ alert: 12, limit: 5 })
    expect(screen.getByRole('heading', { name: 'Last fires' })).toBeInTheDocument()
  })

  it('says when it has not fired', async () => {
    api.events.mockResolvedValue({ events: [], more: false })
    renderIn(<RecentFires alertId={12} now={NOW} />)
    expect(await screen.findByText('Not fired yet.')).toBeInTheDocument()
  })
})
