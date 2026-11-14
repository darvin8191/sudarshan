/**
 * The Risk view and its settings (P7 F7), against lane R's answer shape: each figure with its plain
 * meaning, the service's sentence where there is no figure (never a zero), concentration bars with
 * the limit told in words, overlap with a chosen tab, the emergency fund, and the settings saved
 * in the service's units from the version read.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ risk: vi.fn(), get: vi.fn(), save: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getRisk: api.risk,
  getSettings: api.get,
  saveSettings: api.save,
}))
vi.mock('@/sudarshan/dashboard/MarketCalendar', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/dashboard/MarketCalendar')>()),
  MarketCalendarSource: () => null,
}))
vi.mock('@/sudarshan/watchlist/openGuard', () => ({
  useOpenGuard: () => ({ settling: false, handlers: {} }),
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '../api'
import type { OwnerSettings, RiskReport, TabInfo } from '../types'
import { fallWords, RISK_OFF, RiskView, statText } from './RiskView'
import { formOf, valuesOf } from './SettingsSheet'

const TABS = [
  { id: 4, name: 'Long term', version: 1 },
  { id: 5, name: 'Gold', version: 1 },
] as TabInfo[]
// The answer of a service from before lane G: no `live` rows, so the bars are the service's own
// and say when they were read. `Concentration.test.tsx` holds the bars that move with ticks.
const REPORT = {
  tab_id: 4,
  as_of_day: 20261113,
  basis: 'On closing values up to 13 Nov 2026.',
  risk: {
    days: 248,
    drawdown: {
      pct: '12.3456',
      peak_day: 20260812,
      trough_day: 20260903,
      recovered_day: 20261020,
      note: null,
    },
    volatility: { value: '18.2500', note: null },
    sharpe: { value: '-0.4210', note: null },
    sortino: { value: null, note: 'Shown after a year of history: annualising less misleads.' },
    calmar: { value: '1.0500', note: null },
    concentration: {
      holdings: [
        { label: 'Alpha Industries', weight_pct: '41.2000', over_limit: true },
        { label: 'Beta Bank', weight_pct: '9.5000', over_limit: false },
      ],
      sectors: [{ label: 'Banks', weight_pct: '36.0000', over_limit: true }],
      untagged_pct: '4.0000',
      flags: ['Alpha Industries is 41.2% of this tab, above your 15% limit.'],
    },
    overlap: [{ tab_id: 5, weight_pct: '22.5000', names: ['Alpha Industries', 'Beta Bank'] }],
    emergency: {
      months: '4.5000',
      target_months: 6,
      note: 'Your liquid holdings cover 4.5 of the 6 months you aim for.',
    },
  },
} as unknown as RiskReport
const SETTINGS: OwnerSettings = {
  risk_free_bp: 650,
  max_holding_pct: '15.0000',
  max_sector_pct: '35.0000',
  monthly_expenses_paise: null,
  months_target: 6,
  version: 3,
}

function show() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <RiskView tabs={TABS} />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  api.risk.mockReset().mockResolvedValue({ enabled: true, data: REPORT })
  api.get.mockReset().mockResolvedValue({ enabled: true, data: SETTINGS })
  api.save.mockReset().mockResolvedValue({ enabled: true, data: SETTINGS })
})

describe('the words', () => {
  it('a figure shows two decimals, a percentage its sign only when negative', () => {
    expect(statText('18.2500', true)).toBe('18.25%')
    expect(statText('-0.4210', false)).toBe('−0.42')
    expect(statText(null, true)).toBeNull()
  })

  it('a fall names its days and whether it was recovered', () => {
    expect(fallWords(REPORT.risk.drawdown)).toBe(
      'From 12 Aug 2026 to 3 Sep 2026, recovered 20 Oct 2026'
    )
    expect(fallWords({ ...REPORT.risk.drawdown, recovered_day: null })).toBe(
      'From 12 Aug 2026 to 3 Sep 2026, not yet recovered'
    )
    expect(fallWords({ ...REPORT.risk.drawdown, peak_day: null })).toBeNull()
  })

  it('the settings go in the service’s units, and come back as the owner typed them', () => {
    expect(formOf(SETTINGS)).toEqual({
      rate: '6.5',
      holding: '15',
      sector: '35',
      expenses: '',
      months: '6',
    })
    expect(
      valuesOf({ rate: '7.25', holding: '12.5', sector: '40', expenses: '85,000', months: '9' })
    ).toEqual({
      risk_free_bp: 725,
      max_holding_pct: '12.5',
      max_sector_pct: '40',
      monthly_expenses_paise: 85_000_00,
      months_target: 9,
    })
    expect(valuesOf({ rate: 'x', months: '' })).toMatchObject({
      risk_free_bp: null,
      months_target: null,
    })
  })
})

describe('the view', () => {
  it('shows every figure with its meaning, and a sentence in place of a figure it lacks', async () => {
    show()
    const fall = within(await screen.findByRole('region', { name: 'Largest fall' }))
    expect(fall.getByText('12.35%')).toBeTruthy()
    expect(fall.getByText(/recovered 20 Oct 2026/)).toBeTruthy()
    const stats = within(screen.getByRole('region', { name: 'Swings and ratios' }))
    expect(stats.getByText('18.25%')).toBeTruthy()
    expect(stats.getByText('−0.42')).toBeTruthy()
    expect(
      stats.getByText('Shown after a year of history: annualising less misleads.')
    ).toBeTruthy()
    expect(stats.getByText(/How much the value swings in a year/)).toBeTruthy()
    expect(
      stats.getByText(/On closing values up to 13 Nov 2026\. Rests on 248 trading days\./)
    ).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/Real-time|Live/)
  })

  it('tells a crossed limit in words beside the bar, and says when a snapshot was read', async () => {
    show()
    const card = within(await screen.findByRole('region', { name: 'Concentration' }))
    expect(card.getByText('41.2%')).toBeTruthy()
    expect(card.getAllByText('Above your limit')).toHaveLength(2)
    expect(
      card.getByText('Alpha Industries is 41.2% of this tab, above your 15% limit.')
    ).toBeTruthy()
    expect(card.getByText(/4\.0% has no sector tag/)).toBeTruthy()
    expect(
      card.getByText(
        /^As of \d\d:\d\d:\d\d on \d+ \w+ \d{4}, when this view was read\. These bars do not move with prices; read again for newer values\.$/
      )
    ).toBeTruthy()
    expect(card.queryByText('Real-time')).toBeNull()
  })

  it('works the bars out from the rows the answer carries, and says the second they were read', async () => {
    const row = {
      asset: {
        id: 8,
        name: 'Gold bar',
        kind: 'manual',
        sector: 'Metals',
        exchange: null,
        symbol: null,
      },
      today_mode: 'none',
      price: { price_u: 60_000_000, prev_close_u: null },
      lines: [{ account_id: 1, qty_u: 2_000_000, cost_paise: 1_000_000 }],
    }
    const live = {
      read_at: Date.UTC(2026, 10, 13, 8, 35, 9),
      max_holding_pct: '15.0000',
      max_sector_pct: '100.0000',
      rows: [row],
    }
    api.risk.mockResolvedValue({ enabled: true, data: { ...REPORT, live } })
    show()
    const card = within(await screen.findByRole('region', { name: 'Concentration' }))
    expect(card.getByText('Gold bar')).toBeTruthy()
    expect(card.queryByText('Alpha Industries')).toBeNull() // not the snapshot's bars
    expect(
      card.getByText('Gold bar is 100% of what is held here, above your 15% limit.')
    ).toBeTruthy()
    expect(card.getByText(/are as read at 14:05:09 on 13 Nov 2026\./)).toBeTruthy()
    expect(card.queryByText('Real-time')).toBeNull() // nothing here has a price that ticks
    const fund = within(screen.getByRole('region', { name: 'Emergency fund' }))
    expect(fund.getByText(/As of 14:05:09 on 13 Nov 2026, when this view was read\./)).toBeTruthy()
  })

  it('shows the overlap with the other tab and the emergency fund against its target', async () => {
    show()
    const overlap = within(await screen.findByRole('region', { name: 'Overlap' }))
    expect(overlap.getByRole('option', { name: 'Gold' })).toBeTruthy()
    expect(overlap.getByText('22.5%')).toBeTruthy()
    expect(overlap.getByText('Held in both: Alpha Industries, Beta Bank')).toBeTruthy()
    const fund = within(screen.getByRole('region', { name: 'Emergency fund' }))
    expect(fund.getByText('4.5 months')).toBeTruthy()
    expect(fund.getByText(/your target is 6 months/)).toBeTruthy()
  })

  it('asks for every tab together first, then for the tab chosen', async () => {
    show()
    await screen.findByRole('region', { name: 'Largest fall' })
    expect(api.risk.mock.calls[0][0]).toEqual({})
    await userEvent.selectOptions(screen.getByLabelText('Tab'), 'Long term')
    await waitFor(() => expect(api.risk.mock.lastCall?.[0]).toEqual({ tab: 4 }))
    for (const button of screen.getAllByRole('button'))
      expect(button.className).toContain('min-h-12')
  })

  it('says its own sentence while the switch is off', async () => {
    api.risk.mockRejectedValue(
      new PortfolioError({ code: 'not_ready', message: 'x', entryId: null })
    )
    show()
    expect(await screen.findByText(RISK_OFF)).toBeTruthy()
  })
})

describe('risk settings', () => {
  it('opens on what is kept and saves the change from the version read, then reads the figures again', async () => {
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Risk settings' }))
    const sheet = within(await screen.findByRole('dialog'))
    const expenses = await sheet.findByLabelText(/Monthly expenses, in rupees/)
    expect((sheet.getByLabelText(/Risk-free rate/) as HTMLInputElement).value).toBe('6.5')
    await userEvent.type(expenses, '85000')
    await userEvent.click(sheet.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.save).toHaveBeenCalledWith({
        values: {
          risk_free_bp: 650,
          max_holding_pct: '15',
          max_sector_pct: '35',
          monthly_expenses_paise: 85_000_00,
          months_target: 6,
        },
        version: 3,
      })
    )
    await waitFor(() => expect(api.risk).toHaveBeenCalledTimes(2))
  })

  it('keeps the sheet open with the service’s sentence when a value is refused', async () => {
    api.save.mockRejectedValue(
      new PortfolioError({
        code: 'bad_request',
        message: 'The risk-free rate must be between 0% and 50% a year.',
        entryId: null,
      })
    )
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Risk settings' }))
    const sheet = within(await screen.findByRole('dialog'))
    await userEvent.click(await sheet.findByRole('button', { name: 'Save' }))
    expect((await sheet.findByRole('alert')).textContent).toMatch(/between 0% and 50%/)
  })
})
