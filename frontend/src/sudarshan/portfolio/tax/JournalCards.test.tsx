/**
 * The journal's two parts of the Tax view (P7 F3): "Estimate against actual" month by month from
 * `charges_reconcile`, with a sign's meaning in words and a "Check" chip of icon and word; and the
 * button that asks the journal to build the FY pack and then points to Reports.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('@/sudarshan/journal/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/journal/api')>()),
  journalCall: api.call,
}))

import { createQueryClient } from '@/app/providers'
import { JournalError } from '@/sudarshan/journal/api'
import type { ChargesReconcile, ReconcileMonth } from '@/sudarshan/journal/types'
import {
  BuildPack,
  differenceWords,
  fyKey,
  JOURNAL_OFF,
  RECONCILE_HELP,
  ReconcileBlock,
} from './JournalCards'

function month(over: Partial<ReconcileMonth> & { month: number; label: string }): ReconcileMonth {
  return {
    orders: 4,
    estimate_paise: 1_703_800,
    actual_paise: null,
    diff_paise: null,
    diff_pct: null,
    flagged: false,
    words: null,
    segments: [],
    ...over,
  }
}

const YEAR: ChargesReconcile = {
  fy: 2026,
  fy_label: 'FY 2026-27',
  running: true,
  months: [
    month({ month: 202609, label: 'September 2026', orders: 0, estimate_paise: 0 }),
    month({
      month: 202610,
      label: 'October 2026',
      estimate_paise: 1_703_800,
      actual_paise: 1_231_800,
      diff_paise: -472_000,
      diff_pct: '-38.32',
      flagged: true,
      words: 'Brokerage is ₹4,000.00 below the estimate: check your plan under Reports > Settings.',
    }),
    month({
      month: 202611,
      label: 'November 2026',
      estimate_paise: 851_900,
      words: 'Upstox’s charges for a month are read from the 3rd of the next month.',
    }),
  ],
  year: {
    months: 1,
    estimate_paise: 1_703_800,
    actual_paise: 1_231_800,
    diff_paise: -472_000,
    diff_pct: '-38.32',
    flagged: true,
  },
  note: null,
  rate_card: { version: '2026-11-14.2', from_day: 20260401 },
}

function show(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>
  )
}

// Braces: a function returned from beforeEach is run by vitest as the test's cleanup, and
// `mockReset()` returns the mock itself, which would then be called once more after each test.
beforeEach(() => {
  api.call.mockReset()
})

describe('estimate against actual', () => {
  it('lists the months that have orders, newest first, each with both figures and the difference in words', async () => {
    api.call.mockResolvedValue({ enabled: true, data: YEAR })
    show(<ReconcileBlock fy={2026} />)
    const list = await screen.findByRole('list', { name: 'Estimate against actual, by month' })
    expect(api.call).toHaveBeenCalledWith('charges_reconcile', { fy: 2026 }, expect.anything())
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(2) // September had no orders
    expect(rows[0].textContent).toContain('November 2026')
    expect(rows[0].textContent).toContain('₹8,519.00')
    expect(rows[0].textContent).toContain('Not read yet')
    expect(rows[0].textContent).toContain('read from the 3rd of the next month')
    expect(rows[0].textContent).not.toContain('Difference')
    expect(within(rows[0]).queryByText('Check')).toBeNull()
    expect(rows[1].textContent).toContain('October 2026')
    expect(rows[1].textContent).toContain('₹17,038.00')
    expect(rows[1].textContent).toContain('₹12,318.00')
    expect(rows[1].textContent).toContain('₹4,720.00 less than estimated')
    expect(rows[1].textContent).toContain('Brokerage is ₹4,000.00 below the estimate')
    // The chip is an icon and a word, never a colour alone.
    const chip = within(rows[1]).getByText('Check')
    expect(chip.querySelector('svg')).not.toBeNull()
    expect(screen.getByText(RECONCILE_HELP)).toBeTruthy()
    expect(
      screen.getByText(/1 month compared so far: estimate ₹17,038.00, Upstox’s actual ₹12,318.00/)
    ).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/real-time/i)
  })

  it('says to switch the journal on when the journal is off', async () => {
    api.call.mockResolvedValue({ enabled: false, data: null, message: 'off' })
    const first = show(<ReconcileBlock fy={2026} />)
    expect(await screen.findByText(JOURNAL_OFF)).toBeTruthy()
    first.unmount()
  })

  it('shows the journal’s own sentence when the comparison cannot be had', async () => {
    api.call.mockImplementation(async () => {
      throw new JournalError({
        code: 'unavailable',
        message: 'This part of the journal is not ready yet.',
      })
    })
    show(<ReconcileBlock fy={2026} />)
    expect(await screen.findByText('This part of the journal is not ready yet.')).toBeTruthy()
  })

  it('a year with no trades says so', async () => {
    api.call.mockResolvedValue({
      enabled: true,
      data: {
        ...YEAR,
        months: [],
        note: 'The journal holds no trades of this year, so there is nothing to compare yet.',
      },
    })
    show(<ReconcileBlock fy={2025} />)
    expect(await screen.findByText(/holds no trades of this year/)).toBeTruthy()
  })

  it('a difference says which way it goes', () => {
    expect(differenceWords(24_000)).toBe('₹240.00 more than estimated')
    expect(differenceWords(-24_000)).toBe('₹240.00 less than estimated')
    expect(differenceWords(0)).toBe('Same as the estimate')
  })
})

describe('build the FY pack', () => {
  it('asks the journal for the year’s pack once and points to Reports', async () => {
    const user = userEvent.setup()
    let finish: (value: unknown) => void = () => {}
    api.call.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    show(<BuildPack fy={2026} />)
    const button = screen.getByRole('button', { name: 'Build the FY pack (PDF)' })
    expect(button.className).toContain('min-h-12')
    await user.click(button)
    await user.click(screen.getByRole('button', { name: 'Building the FY pack' }))
    expect(api.call).toHaveBeenCalledTimes(1)
    expect(api.call).toHaveBeenCalledWith('report_build', { kind: 'fy', period: 'FY2627' })
    finish({ enabled: true, data: { id: 9, version: 1, words: 'The tax pack is in the archive.' } })
    expect(await screen.findByText('The tax pack is in the archive.')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open Reports' }).getAttribute('href')).toBe('/reports')
  })

  it('shows the journal’s own sentence when it could not build it', async () => {
    const user = userEvent.setup()
    api.call.mockImplementation(async () => {
      throw new JournalError({ code: 'refused', message: 'That period has not started yet.' })
    })
    show(<BuildPack fy={2026} />)
    await user.click(screen.getByRole('button', { name: 'Build the FY pack (PDF)' }))
    expect(await screen.findByText('That period has not started yet.')).toBeTruthy()
    expect(screen.queryByRole('link', { name: 'Open Reports' })).toBeNull()
    api.call.mockResolvedValue({ enabled: false, data: null, message: 'off' })
    await user.click(screen.getByRole('button', { name: 'Build the FY pack (PDF)' }))
    await waitFor(() =>
      expect(
        screen.getByText(/switched off on the server, so it cannot build the pack/)
      ).toBeTruthy()
    )
  })

  it('names the year as the journal keys it', () => {
    expect(fyKey(2026)).toBe('FY2627')
    expect(fyKey(1999)).toBe('FY9900')
    expect(fyKey(2009)).toBe('FY0910')
  })
})
