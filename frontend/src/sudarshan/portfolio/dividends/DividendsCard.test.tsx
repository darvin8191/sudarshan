/**
 * The Dividends card and its confirm sheet (P7 F5), against lane C's answer shape: expected against
 * confirmed by payer, the likely-TDS flag in words, a line's state as icon and word, the owner's
 * confirmation in the service's units, and the card's own sentence while insights are off.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn(), set: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getDividends: api.get,
  setDividend: api.set,
}))
vi.mock('@/sudarshan/watchlist/openGuard', () => ({
  useOpenGuard: () => ({ settling: false, handlers: {} }),
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '../api'
import type { DividendLine, DividendsReport } from '../types'
import { DIVIDENDS_OFF, DividendsCard, lineAmount } from './DividendsCard'

const line = (more: Partial<DividendLine>): DividendLine => ({
  id: 3,
  member_id: 1,
  account_id: 1,
  isin: 'INE000B01018',
  name: 'Beta Bank',
  event_id: 8,
  fy: 2026,
  ex_day: 20260812,
  qty_u: 300_000_000,
  expected_paise: 1_65_000,
  state: 'expected',
  credited_paise: null,
  tds_paise: null,
  credited_day: null,
  version: 4,
  ...more,
})
const REPORT: DividendsReport = {
  fy: 2026,
  member_id: 1,
  as_of: Date.UTC(2026, 10, 14, 10),
  dividends: {
    payers: [
      {
        isin: 'INE000B01018',
        name: 'Beta Bank',
        lines: [
          line({}),
          line({
            id: 4,
            ex_day: 20260505,
            state: 'confirmed',
            credited_paise: 9_00_000,
            tds_paise: 1_00_000,
          }),
        ],
        expected_paise: 12_34_56_78_901_23,
        confirmed_paise: 10_00_000,
        tds_likely: true,
        tds_expected_paise: 1_16_500,
      },
    ],
    total_paise: 11_65_000,
    tds_expected_paise: 1_16_500,
    unconfirmed_count: 1,
  },
}

function show(on = true) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <DividendsCard fy={2026} member={undefined} on={on} />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  api.get.mockReset().mockResolvedValue({ enabled: true, data: REPORT })
  api.set.mockReset().mockResolvedValue({ enabled: true, data: line({}) })
})

describe('the card', () => {
  it('counts a line as paid once confirmed, nothing when not received, else as expected', () => {
    expect(lineAmount(line({}))).toBe(1_65_000)
    expect(lineAmount(line({ state: 'confirmed', credited_paise: 900, tds_paise: 100 }))).toBe(1000)
    expect(lineAmount(line({ state: 'not_received' }))).toBe(0)
  })

  it('shows the year, each payer’s expected against confirmed, the TDS flag in words and each state', async () => {
    show()
    const card = within(await screen.findByRole('region', { name: 'Dividends' }))
    expect(await card.findByText('₹11,650.00')).toBeTruthy()
    expect(card.getByText(/1 not confirmed yet/)).toBeTruthy()
    const whole = card.getByText('₹12,34,56,78,901.23')
    expect(whole.className).toContain('whitespace-nowrap')
    expect(card.getByText('Expected in all · confirmed ₹10,000.00')).toBeTruthy()
    expect(card.getByText(/TDS likely: about ₹1,165.00\. Check Form 26AS\./)).toBeTruthy()
    expect(card.getByText('Expected')).toBeTruthy()
    expect(card.getByText('Credited')).toBeTruthy()
    expect(card.getByText(/^As of 14 Nov 2026, 15:30/)).toBeTruthy()
    for (const button of card.getAllByRole('button')) expect(button.className).toContain('min-h-12')
    expect(document.body.textContent).not.toMatch(/Real-time|Live/)
  })

  it('says its own sentence while insights are off, and asks the service nothing', async () => {
    show(false)
    expect(screen.getByText(DIVIDENDS_OFF)).toBeTruthy()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('says the same when the op answers that it is off', async () => {
    api.get.mockRejectedValue(
      new PortfolioError({ code: 'not_ready', message: 'x', entryId: null })
    )
    show()
    expect(await screen.findByText(DIVIDENDS_OFF)).toBeTruthy()
  })
})

describe('the confirm sheet', () => {
  it('opens on the expected amount and sends what reached the bank, the TDS and the day', async () => {
    show()
    await userEvent.click(await screen.findByRole('button', { name: /12 Aug 2026: Expected/ }))
    const sheet = within(await screen.findByRole('dialog'))
    const credited = sheet.getByLabelText('Credited to your bank, in rupees') as HTMLInputElement
    expect(credited.value).toBe('1650')
    await userEvent.clear(credited)
    await userEvent.type(credited, '1,485')
    await userEvent.type(sheet.getByLabelText(/Tax deducted/), '165')
    await userEvent.click(sheet.getByRole('button', { name: 'Confirm as credited' }))
    await waitFor(() =>
      expect(api.set).toHaveBeenCalledWith({
        id: 3,
        version: 4,
        state: 'confirmed',
        credited_paise: 1_48_500,
        tds_paise: 16_500,
        credited_day: null,
      })
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(api.get).toHaveBeenCalledTimes(2) // read again after the save
  })

  it('marks a line not received, and offers the way back only on a line already decided', async () => {
    show()
    await userEvent.click(await screen.findByRole('button', { name: /5 May 2026: Credited/ }))
    const sheet = within(await screen.findByRole('dialog'))
    expect(sheet.getByRole('button', { name: 'Back to expected' })).toBeTruthy()
    await userEvent.click(sheet.getByRole('button', { name: 'Not received' }))
    await waitFor(() =>
      expect(api.set).toHaveBeenCalledWith({ id: 4, version: 4, state: 'not_received' })
    )
  })

  it('keeps the sheet open with the service’s sentence when it refuses', async () => {
    api.set.mockRejectedValue(
      new PortfolioError({
        code: 'refused',
        message: 'This dividend is an entry in one of your tabs. Change the entry there.',
        entryId: 12,
      })
    )
    show()
    await userEvent.click(await screen.findByRole('button', { name: /12 Aug 2026: Expected/ }))
    const sheet = within(await screen.findByRole('dialog'))
    expect(sheet.queryByRole('button', { name: 'Back to expected' })).toBeNull()
    await userEvent.click(sheet.getByRole('button', { name: 'Confirm as credited' }))
    expect((await sheet.findByRole('alert')).textContent).toMatch(/entry in one of your tabs/)
  })
})
