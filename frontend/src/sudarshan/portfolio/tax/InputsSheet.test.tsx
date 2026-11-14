/**
 * The tax inputs sheet (P7 F2, G1, G2): the owner's losses beside SUDARSHAN's own figure, the
 * 31 Jan 2018 prices the paper waits for, days marked as delivery, purchases from before Upstox's
 * records, buyback tenders, the readings of the open rules and how each account counts; nothing
 * is saved before its dry run has spoken, and a changed field asks for the dry run again.
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
import {
  draftOf,
  fmvWanted,
  newDraft,
  READINGS,
  readingWords,
  rowOf,
  settingsDraft,
} from './inputs'

const INPUTS: TaxInputs = {
  member_id: 1,
  accounts: [
    { id: 1, name: 'Upstox', kind: 'upstox', tax_mode: 'reports', version: 1 },
    { id: 2, name: 'Old demat', kind: 'demat', tax_mode: 'ledger', version: 4 },
    { id: 3, name: 'Gold locker', kind: 'other', tax_mode: 'none', version: 2 },
  ],
  opening_lots: [
    {
      id: 11,
      member_id: 1,
      account_id: 2,
      isin: 'INE111A01025',
      acquired_day: 20160310,
      qty_u: 150_000_000,
      cost_paise: 12_34_567_00,
      source: 'owner',
      note: 'From the contract note',
      version: 2,
    },
    {
      id: 12,
      member_id: 1,
      account_id: 1,
      isin: 'INE222A01017',
      acquired_day: 20190705,
      qty_u: 20_000_000,
      cost_paise: 40_000_00,
      source: 'upstox_pair',
      note: null,
      version: 1,
    },
  ],
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
  trade_marks: [
    {
      id: 13,
      member_id: 1,
      account_id: 1,
      isin: 'INE333A01019',
      day: 20260715,
      qty_u: 5_000_000,
      price_u: 30_000_000,
      mark: 'buyback',
      trade_id: null,
      version: 1,
    },
  ],
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

  it('a purchase, a tender and the readings go in the service’s units too', () => {
    const lot = { account_id: '1', isin: 'INE111A01025', acquired_day: '2016-03-10' }
    expect(
      rowOf(newDraft('opening_lot', { ...lot, qty: '150.5', cost: '1,234.50', note: ' old ' }), 1)
    ).toEqual({
      member_id: 1,
      account_id: 1,
      isin: 'INE111A01025',
      acquired_day: 20160310,
      qty_u: 150_500_000,
      cost_paise: 123_450,
      note: 'old',
    })
    const tender = { account_id: '1', isin: 'INE333A01019', day: '2026-07-15' }
    expect(rowOf(newDraft('trade_mark', { ...tender, qty: '5', price: '3,000' }), 1)).toEqual({
      member_id: 1,
      account_id: 1,
      isin: 'INE333A01019',
      day: 20260715,
      qty_u: 5_000_000,
      price_u: 30_000_000,
      trade_id: null,
    })
    expect(rowOf(newDraft('opening_lot', { qty: 'many' }), 1)).toMatchObject({
      qty_u: null,
      cost_paise: null,
      acquired_day: null,
      note: null,
    })
    // The usual reading is kept as "not chosen" (null), so the rule's own default stands.
    expect(settingsDraft(INPUTS.settings)).toEqual({
      kind: 'settings',
      version: 0,
      fields: { turnover_method: '', exempt_order: '', lt_boundary: '', allocate_expenses: 'yes' },
    })
    const chosen = { ...INPUTS.settings, turnover_method: 'abs_pnl' as const, version: 3 }
    expect(settingsDraft(chosen).fields.turnover_method).toBe('')
    expect(readingWords(READINGS[0], chosen)).toBe(
      'Sum of profits and losses, ICAI method (the usual reading)'
    )
    const draft = settingsDraft(INPUTS.settings)
    draft.fields.lt_boundary = 'on_anniversary'
    draft.fields.allocate_expenses = 'no'
    expect(rowOf(draft, 1)).toEqual({
      member_id: 1,
      turnover_method: null,
      exempt_order: null,
      lt_boundary: 'on_anniversary',
      allocate_expenses: false,
    })
    expect(draftOf('opening_lot', INPUTS.opening_lots[0])).toEqual({
      kind: 'opening_lot',
      id: 11,
      version: 2,
      fields: {
        account_id: '2',
        isin: 'INE111A01025',
        acquired_day: '2016-03-10',
        qty: '150',
        cost: '1234567',
        note: 'From the contract note',
      },
    })
    expect(draftOf('trade_mark', INPUTS.trade_marks[0]).fields).toEqual({
      account_id: '1',
      isin: 'INE333A01019',
      day: '2026-07-15',
      qty: '5',
      price: '3000',
      trade_id: '',
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
    // The Upstox account's way is not the owner's to set: its row opens nothing.
    expect(accounts.getAllByRole('button').map((b) => b.textContent)).toEqual([
      expect.stringContaining('Old demat'),
      expect.stringContaining('Gold locker'),
    ])
  })

  it('sets an account of his own as not for tax only after the dry run, from its version', async () => {
    api.save.mockResolvedValue({
      enabled: true,
      data: {
        kind: 'account_mode',
        row: null,
        effect: ['In FY 2026-27 this changes short-term gain from Rs 4,994.88 to Rs 0.'],
      },
    })
    const refreshed = show()
    const accounts = within(await screen.findByRole('region', { name: 'Accounts' }))
    await userEvent.click(accounts.getByRole('button', { name: /Old demat/ }))
    expect(screen.getByRole('heading', { name: 'How this account counts' })).toBeTruthy()
    const choice = screen.getByLabelText('How it counts for tax') as HTMLSelectElement
    expect(choice.value).toBe('ledger')
    expect(
      within(choice)
        .getAllByRole('option')
        .map((o) => o.textContent)
    ).toEqual(['Choose', 'Counted from its entries', 'Not for tax'])
    expect(screen.queryByRole('button', { name: 'Remove this entry' })).toBeNull()
    await userEvent.selectOptions(choice, 'none')
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'See what it changes' }))
    const effect = within(await screen.findByRole('region', { name: 'What this changes' }))
    expect(effect.getByText(/short-term gain from Rs 4,994.88 to Rs 0/)).toBeTruthy()
    const row = { member_id: 1, account_id: 2, tax_mode: 'none' }
    expect(api.save).toHaveBeenLastCalledWith({
      kind: 'account_mode',
      row,
      id: 2,
      version: 4,
      dry_run: true,
      fy: 2026,
    })
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(refreshed).toHaveBeenCalled())
    expect(api.save.mock.lastCall?.[0]).toMatchObject({ kind: 'account_mode', row, version: 4 })
    expect(api.save.mock.lastCall?.[0].dry_run).toBeUndefined()
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

  it('lists the purchases with their cost whole; Upstox’s own pair is read only', async () => {
    show()
    const lots = within(
      await screen.findByRole('region', { name: 'Purchases before Upstox’s records' })
    )
    expect(lots.getByText('150 bought 10 Mar 2016 · Old demat')).toBeTruthy()
    expect(lots.getByText('₹12,34,567.00').className).toContain('whitespace-nowrap')
    expect(lots.getByText('20 bought 5 Jul 2019 · From Upstox’s report')).toBeTruthy()
    expect(lots.getAllByRole('button')).toHaveLength(2) // the owner's row and Add
  })

  it('a purchase is added only after its dry run', async () => {
    api.save.mockResolvedValue({
      enabled: true,
      data: {
        kind: 'opening_lot',
        row: null,
        effect: [
          'In FY 2026-27 this changes the number of sales with no purchase record from 1 to 0.',
        ],
      },
    })
    show()
    const lots = within(
      await screen.findByRole('region', { name: 'Purchases before Upstox’s records' })
    )
    await userEvent.click(lots.getByRole('button', { name: 'Add a purchase' }))
    await userEvent.selectOptions(screen.getByLabelText('Account'), '1')
    await userEvent.type(screen.getByLabelText(/ISIN of the share/), 'INE444A01011')
    await userEvent.type(screen.getByLabelText('Day it was bought'), '2015-08-03')
    await userEvent.type(screen.getByLabelText(/Quantity bought/), '40')
    await userEvent.type(screen.getByLabelText(/What it cost in all/), '18,400')
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'See what it changes' }))
    expect(await screen.findByText(/sales with no purchase record from 1 to 0/)).toBeTruthy()
    const row = {
      member_id: 1,
      account_id: 1,
      isin: 'INE444A01011',
      acquired_day: 20150803,
      qty_u: 40_000_000,
      cost_paise: 18_400_00,
      note: null,
    }
    expect(api.save).toHaveBeenLastCalledWith({
      kind: 'opening_lot',
      row,
      id: undefined,
      version: undefined,
      dry_run: true,
      fy: 2026,
    })
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.save.mock.lastCall?.[0].dry_run).toBeUndefined())
    expect(api.save.mock.lastCall?.[0]).toMatchObject({ kind: 'opening_lot', row })
  })

  it('a kept tender opens filled, is checked from its version and can be removed', async () => {
    api.save.mockResolvedValue({
      enabled: true,
      data: { kind: 'trade_mark', row: null, effect: ['This changes no figure of FY 2026-27.'] },
    })
    api.remove.mockResolvedValue({ enabled: true, data: { id: 13 } })
    show()
    const tenders = within(await screen.findByRole('region', { name: 'Buyback tenders' }))
    expect(tenders.getByText('5 at ₹3,000.00 · Upstox')).toBeTruthy()
    await userEvent.click(tenders.getByRole('button', { name: /INE333A01019 on 15 Jul 2026/ }))
    expect((screen.getByLabelText(/Buyback price for one share/) as HTMLInputElement).value).toBe(
      '3000'
    )
    await userEvent.type(screen.getByLabelText(/Upstox’s trade id/), '50011')
    await userEvent.click(screen.getByRole('button', { name: 'See what it changes' }))
    await screen.findByRole('button', { name: 'Save' })
    expect(api.save.mock.lastCall?.[0]).toMatchObject({
      kind: 'trade_mark',
      id: 13,
      version: 1,
      dry_run: true,
      row: { day: 20260715, qty_u: 5_000_000, price_u: 30_000_000, trade_id: '50011' },
    })
    await userEvent.click(screen.getByRole('button', { name: 'Remove this entry' }))
    await userEvent.click(screen.getByRole('button', { name: 'Yes, remove this entry' }))
    await waitFor(() =>
      expect(api.remove).toHaveBeenCalledWith({ id: 13, version: 1, kind: 'trade_mark' })
    )
  })

  it('the readings say the usual one until changed, and a change is checked from its version', async () => {
    api.save.mockResolvedValue({
      enabled: true,
      data: {
        kind: 'settings',
        row: null,
        effect: ['In FY 2026-27 this changes long-term gain from Rs 5,986 to Rs 0.'],
      },
    })
    show()
    const readings = within(
      await screen.findByRole('region', { name: 'Readings of the open rules' })
    )
    expect(
      readings.getByText('Sum of profits and losses, ICAI method (the usual reading)')
    ).toBeTruthy()
    expect(readings.getByText('Still short-term (the usual reading)')).toBeTruthy()
    await userEvent.click(readings.getByRole('button', { name: /Turnover of F&O and intraday/ }))
    const turnover = screen.getByLabelText('Turnover of F&O and intraday') as HTMLSelectElement
    expect(
      within(turnover)
        .getAllByRole('option')
        .map((o) => o.textContent)
    ).toEqual([
      'Sum of profits and losses, ICAI method (the usual reading)',
      'Sum of profits and losses plus premium on options sold',
    ])
    await userEvent.selectOptions(turnover, 'abs_pnl_plus_sale_premium')
    await userEvent.selectOptions(
      screen.getByLabelText('Share-market charges and capital gains'),
      'no'
    )
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'See what it changes' }))
    await screen.findByRole('button', { name: 'Save' })
    expect(api.save).toHaveBeenLastCalledWith({
      kind: 'settings',
      row: {
        member_id: 1,
        turnover_method: 'abs_pnl_plus_sale_premium',
        exempt_order: null,
        lt_boundary: null,
        allocate_expenses: false,
      },
      id: undefined,
      version: 0,
      dry_run: true,
      fy: 2026,
    })
    expect(screen.queryByRole('button', { name: 'Remove this entry' })).toBeNull()
  })
})
