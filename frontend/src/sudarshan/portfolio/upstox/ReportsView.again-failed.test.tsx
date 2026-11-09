/**
 * "Read again" is a mutation, and the report views drew only the query's error: with the bridge or
 * the service down at the tap, the spinner stopped and nothing was said. The sentence is the
 * bridge's own, and it belongs to the year it was asked for.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '../api'
import { PnlView, TradesView } from './ReportsView'

const DOWN =
  'The portfolio service is not answering. It restarts on its own; try again in a minute.'
const YEARS = {
  today: 20261109,
  years: [
    { fy: 2026, label: '2026-27', current: true },
    { fy: 2025, label: '2025-26', current: false },
  ],
}

function answer(op: string, args: { force?: boolean; fy?: number }) {
  if (op === 'report_years') return { enabled: true, data: YEARS }
  if (args.force) throw new PortfolioError({ code: 'unavailable', message: DOWN, entryId: null })
  const base = {
    state: 'ok',
    read_at: Date.UTC(2026, 10, 9, 4, 0),
    note: null,
    fy: args.fy,
    segment: 'EQ',
  }
  return op === 'report_trades'
    ? {
        enabled: true,
        data: { ...base, rows: [], total_records: 0, bought_paise: 0, sold_paise: 0 },
      }
    : {
        enabled: true,
        data: {
          ...base,
          rows: [],
          trades_count: 0,
          realised_paise: 0,
          charges: null,
          net_paise: null,
        },
      }
}

describe('a Read again that fails', () => {
  it.each([
    ['trades', TradesView],
    ['P&L', PnlView],
  ] as const)('says so on the %s view, and not on another year', async (_name, View) => {
    api.call.mockImplementation(async (op: string, args: { force?: boolean; fy?: number }) =>
      answer(op, args ?? {})
    )
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={createQueryClient()}>
        <View />
      </QueryClientProvider>
    )
    await user.click(await screen.findByRole('button', { name: /read again/i }))
    await waitFor(() => expect(screen.getByText(DOWN)).toBeTruthy())
    await user.click(screen.getByRole('button', { name: /2025-26/ }))
    await waitFor(() => expect(screen.queryByText(DOWN)).toBeNull())
  })
})
