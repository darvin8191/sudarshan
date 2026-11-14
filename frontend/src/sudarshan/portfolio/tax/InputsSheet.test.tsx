/**
 * The tax inputs sheet (P7 F2): the owner's losses beside SUDARSHAN's own figure, the 31 Jan 2018
 * prices the paper waits for, days marked as delivery and how each account counts; nothing is
 * saved before its dry run has spoken, and a changed field asks for the dry run again.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ inputs: vi.fn(), save: vi.fn(), remove: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getTaxInputs: api.inputs,
  saveTaxInput: api.save,
  deleteTaxInput: api.remove,
}))
vi.mock('@/sudarshan/watchlist/openGuard', () => ({
  useOpenGuard: () => ({ settling: false, handlers: {} }),
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '../api'
import type { TaxInputs, TaxPack } from '../types'
import { InputsSheet } from './InputsSheet'
import { draftOf, fmvWanted, newDraft, rowOf } from './inputs'

const INPUTS: TaxInputs = {
  member_id: 1,
  accounts: [
    { id: 1, name: 'Upstox', kind: 'upstox', tax_mode: 'reports' },
    { id: 2, name: 'Old demat', kind: 'demat', tax_mode: 'ledger' },
    { id: 3, name: 'Gold locker', kind: 'other', tax_mode: 'none' },
  ] as TaxInputs['accounts'],
  opening_lots: [],
  fmvs: [{ id: 5, isin: 'INE002A01018', fmv_u: 9_215_500, source: 'owner', version: 2 }],
  carries: [
    {
      id: 7,
      member_id: 1,
      loss_fy: 2024,
      bucket: 'stcl',
      paise: 98_76_54_321_00,
      source: 'owner',
      note: null,
      version: 3,
    },
    {
      id: 8,
      member_id: 1,
      loss_fy: 2025,
      bucket: 'business',
      paise: 4_000_00,
      source: 'computed',
      note: null,
      version: 1,
    },
  ],
  day_overrides: [
    {
      id: 9,
      member_id: 1,
      account_id: 1,
      isin: 'INE009A01021',
      day: 20260612,
      treat: 'delivery',
      version: 1,
    },
  ],
  trade_marks: [],
  settings: {
    member_id: 1,
    turnover_method: null,
    exempt_order: null,
    lt_boundary: null,
    allocate_expenses: true,
    version: 0,
  },
}
const PACK = {
  capital_gains: {
    own_figures: [
      { loss_fy: 2024, bucket: 'stcl', paise: 12_000_00, source: 'computed', last_fy: 2032 },
    ],
    needs_fmv: [{ isin: 'INE040A01034' }, { isin: 'INE040A01034' }, { isin: 'INE002A01018' }],
  },
  business: { own_figures: [] },
} as unknown as TaxPack

function show(onClose = () => {}) {
  const client = createQueryClient()
  const spy = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <InputsSheet open onClose={onClose} pack={PACK} member={undefined} fy={2026} />
    </QueryClientProvider>
  )
  return spy
}

beforeEach(() => {
  for (const mock of Object.values(api)) mock.mockReset()
  api.inputs.mockResolvedValue({ enabled: true, data: INPUTS })
})

describe('the rows the service is asked to keep', () => {
  it('are in its own units, and a text that is no number goes as null', () => {
    expect(
      rowOf(newDraft('carry', { loss_fy: '2024', bucket: 'ltcl', amount: '1,25,000.50' }), 4)
    ).toEqual({
      member_id: 4,
      loss_fy: 2024,
      bucket: 'ltcl',
      paise: 12_500_050,
      note: null,
    })
    expect(rowOf(newDraft('fmv', { isin: 'INE002A01018', price: '921.55' }), 1)).toEqual({
      isin: 'INE002A01018',
      fmv_u: 9_215_500,
    })
    expect(
      rowOf(newDraft('day_override', { account_id: '2', isin: 'X', day: '2026-06-12' }), 1)
    ).toEqual({
      member_id: 1,
      account_id: 2,
      isin: 'X',
      day: 20260612,
    })
    expect(rowOf(newDraft('carry', { amount: 'ten' }), 1)).toMatchObject({
      paise: null,
      loss_fy: null,
    })
  })

  it('a kept row opens with what it holds and its version', () => {
    expect(draftOf('carry', INPUTS.carries[0])).toEqual({
      kind: 'carry',
      id: 7,
      version: 3,
      fields: { loss_fy: '2024', bucket: 'stcl', amount: '987654321' },
    })
    expect(draftOf('fmv', INPUTS.fmvs[0]).fields).toEqual({ isin: 'INE002A01018', price: '921.55' })
  })

  it('the prices wanted are the shares with a waiting sale and no price yet, once each', () => {
    expect(fmvWanted(PACK, INPUTS)).toEqual(['INE040A01034'])
  })
})

describe('the sheet', () => {
  it('lists the owner’s loss beside SUDARSHAN’s own figure, whole, and the computed one apart', async () => {
    show()
    const losses = within(await screen.findByRole('region', { name: 'Losses brought forward' }))
    expect(losses.getByText('Your figure is used. SUDARSHAN’s own figure: ₹12,000.00')).toBeTruthy()
    const figure = losses.getByText('₹98,76,54,321.00')
    expect(figure.className).toContain('whitespace-nowrap')
    expect(losses.getByText('SUDARSHAN’s own figure, from the years it can read')).toBeTruthy()
    expect(losses.getAllByRole('button')).toHaveLength(2) // the owner's row and Add; not the computed one
    for (const button of screen.getAllByRole('button')) {
      if (button.textContent !== 'Close') expect(button.className).toContain('min-h-12')
    }
  })

  it('names how each account counts, a tab account left out as Not for tax', async () => {
    show()
    const accounts = within(await screen.findByRole('region', { name: 'Accounts' }))
    expect(accounts.getByText(/Counted from Upstox’s reports/)).toBeTruthy()
    expect(accounts.getByText(/Counted from its entries/)).toBeTruthy()
    expect(accounts.getByText(/Not for tax/)).toBeTruthy()
  })

  it('a price the paper waits for opens its form filled; the dry run speaks before Save exists', async () => {
    api.save.mockResolvedValue({
      enabled: true,
      data: {
        kind: 'fmv',
        row: null,
        effect: ['In FY 2026-27 this changes long-term gain from Rs 30,000 to Rs 15,000.'],
      },
    })
    const refreshed = show()
    const prices = within(await screen.findByRole('region', { name: 'Prices of 31 January 2018' }))
    await userEvent.click(prices.getByRole('button', { name: /INE040A01034/ }))
    expect((screen.getByLabelText(/ISIN of the share/) as HTMLInputElement).value).toBe(
      'INE040A01034'
    )
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    await userEvent.type(screen.getByLabelText(/Highest price on 31 January 2018/), '1,428.25')
    await userEvent.click(screen.getByRole('button', { name: 'See what it changes' }))
    const effect = within(await screen.findByRole('region', { name: 'What this changes' }))
    expect(effect.getByText(/from Rs 30,000 to Rs 15,000/)).toBeTruthy()
    expect(effect.getByText('Nothing is saved until you tap Save.')).toBeTruthy()
    expect(api.save).toHaveBeenLastCalledWith({
      kind: 'fmv',
      row: { isin: 'INE040A01034', fmv_u: 14_282_500 },
      id: undefined,
      version: undefined,
      dry_run: true,
      fy: 2026,
    })
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(refreshed).toHaveBeenCalled())
    const saved = api.save.mock.lastCall?.[0]
    expect(saved.dry_run).toBeUndefined()
    expect(saved.request_id).toMatch(/[0-9a-f-]{36}/)
    expect(await screen.findByRole('region', { name: 'Losses brought forward' })).toBeTruthy()
  })

  it('a changed field takes Save away until the dry run is run again', async () => {
    api.save.mockResolvedValue({
      enabled: true,
      data: { kind: 'carry', row: null, effect: ['This changes no figure of FY 2026-27.'] },
    })
    show()
    const losses = within(await screen.findByRole('region', { name: 'Losses brought forward' }))
    await userEvent.click(
      losses.getByRole('button', { name: /Short-term capital loss of FY 2024-25/ })
    )
    await userEvent.click(screen.getByRole('button', { name: 'See what it changes' }))
    await screen.findByRole('button', { name: 'Save' })
    expect(api.save.mock.lastCall?.[0]).toMatchObject({ id: 7, version: 3, dry_run: true })
    await userEvent.type(screen.getByLabelText(/Loss carried forward, in rupees/), '5')
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'What this changes' })).toBeNull()
  })

  it('shows the service’s sentence when it refuses a value, and saves nothing', async () => {
    api.save.mockRejectedValue(
      new PortfolioError({
        code: 'bad_request',
        message: 'Enter the loss as an amount above zero.',
        entryId: null,
      })
    )
    show()
    const losses = within(await screen.findByRole('region', { name: 'Losses brought forward' }))
    await userEvent.click(losses.getByRole('button', { name: 'Add a loss from a filed return' }))
    await userEvent.click(screen.getByRole('button', { name: 'See what it changes' }))
    expect((await screen.findByRole('alert')).textContent).toBe(
      'Enter the loss as an amount above zero.'
    )
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
  })

  it('removes a kept row only after a second tap, at the version it was read', async () => {
    api.remove.mockResolvedValue({ enabled: true, data: { id: 9 } })
    show()
    const days = within(await screen.findByRole('region', { name: 'Days counted as delivery' }))
    await userEvent.click(days.getByRole('button', { name: /INE009A01021 on 12 Jun 2026/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove this entry' }))
    expect(api.remove).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Yes, remove this entry' }))
    await waitFor(() =>
      expect(api.remove).toHaveBeenCalledWith({ id: 9, version: 1, kind: 'day_override' })
    )
  })

  it('offers only the accounts that count when a day is marked', async () => {
    show()
    const days = within(await screen.findByRole('region', { name: 'Days counted as delivery' }))
    await userEvent.click(days.getByRole('button', { name: 'Mark a day as delivery' }))
    const options = within(screen.getByLabelText('Account')).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(['Choose', 'Upstox', 'Old demat'])
  })
})
