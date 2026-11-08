/**
 * The price sheet (M7.39): the field's words by what is set, the text as the service's price_u, a
 * loan's balance sent with its tab, a future date refused before anything is sent.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))

import { createQueryClient } from '@/app/providers'
import { fieldWords, PriceSheet, type PriceTarget, priceOf } from './PriceSheet'

const GOLD: PriceTarget = {
  asset_id: 3,
  name: 'Gold coin',
  kind: 'manual',
  unit_label: 'g',
  tab_id: 2,
}
const FUND: PriceTarget = {
  asset_id: 4,
  name: 'Some Fund',
  kind: 'mf',
  unit_label: null,
  tab_id: 2,
}
const LOAN: PriceTarget = {
  asset_id: 9,
  name: 'Home loan',
  kind: 'liability',
  unit_label: null,
  tab_id: 2,
}

function show(target: PriceTarget, onClose = vi.fn()) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <PriceSheet open target={target} onClose={onClose} />
    </QueryClientProvider>
  )
  return onClose
}

beforeEach(() => {
  api.call.mockReset()
  api.call.mockResolvedValue({ enabled: true, data: {} })
})

describe('the price sheet', () => {
  it('words the field by what is set', () => {
    expect(fieldWords(GOLD).label).toBe('Value (₹ per g)')
    expect(fieldWords(FUND).label).toBe('NAV (₹ per unit)')
    expect(fieldWords(LOAN).label).toBe('Outstanding balance (₹)')
    expect(fieldWords(LOAN).hint).toContain('0 once the loan is paid off')
  })

  it('turns the text into price_u without a float: four decimals for a value, paise for a loan', () => {
    expect(priceOf(GOLD, '6,000.1234')).toBe(60_001_234)
    expect(priceOf(GOLD, '0')).toBeNull() // a value is above zero
    expect(priceOf(GOLD, 'abc')).toBeNull()
    expect(priceOf(FUND, '123.4567')).toBe(1_234_567)
    expect(priceOf(LOAN, '48,00,000')).toBe(48_000_000_000) // rupees x 10^4
    expect(priceOf(LOAN, '0')).toBe(0) // paid off
    expect(priceOf(LOAN, '12.345')).toBeNull() // a balance has paise at most
  })

  it('sends a loan balance with its tab and closes', async () => {
    const onClose = show(LOAN)
    expect(screen.getByText('Update the balance')).toBeTruthy()
    await userEvent.type(screen.getByLabelText('Outstanding balance (₹)'), '4800000')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(api.call).toHaveBeenCalledWith(
      'price_point_set',
      expect.objectContaining({ id: 9, price_u: 48_000_000_000, tab_id: 2 })
    )
    const sent = api.call.mock.calls[0][1]
    expect(sent.day).toBeGreaterThan(20260000)
  })

  it('sends a value without a tab, and refuses a future date before sending', async () => {
    show(GOLD)
    await userEvent.type(screen.getByLabelText('Value (₹ per g)'), '6100')
    const date = screen.getByLabelText('On')
    await userEvent.clear(date)
    await userEvent.type(date, '2099-01-01')
    expect(screen.getByText('A value cannot be dated after today.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)
    expect(api.call).not.toHaveBeenCalled()
    await userEvent.clear(date)
    await userEvent.type(date, '2026-10-20')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.call).toHaveBeenCalled())
    expect(api.call.mock.calls[0][1]).toEqual({ id: 3, day: 20261020, price_u: 61_000_000 })
  })

  it('shows the service sentence when it refuses, and stays open', async () => {
    api.call.mockResolvedValue({ enabled: false, message: 'Your portfolio is off right now.' })
    const onClose = show(FUND)
    await userEvent.type(screen.getByLabelText('NAV (₹ per unit)'), '125')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Your portfolio is off right now.')).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
  })
})
