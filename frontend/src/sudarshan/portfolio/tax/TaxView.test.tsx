/**
 * The Tax view (P7 F1) against the tax core's own fixture: the per-head cards with the pack's
 * figures, the schedule sheet, what needs the owner, the working-paper notice, the age of the
 * read (never "Real-time"), the switch-off sentence, and a large figure kept in one piece.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
  getTaxInputs: (args: object) => api.call('tax_inputs', args),
  getDividends: (args: object) => api.call('dividends', args),
}))

// The journal's two parts of the view have their own tests (JournalCards.test.tsx); here the
// journal is switched off, so nothing of this file goes to the network for them.
vi.mock('@/sudarshan/journal/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/journal/api')>()),
  journalCall: async () => ({ enabled: false, data: null, message: 'off' }),
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '../api'
import { DIVIDENDS_OFF } from '../dividends/DividendsCard'
import type { Member, TaxPack } from '../types'
import { asOf, fyLabel, fyOfDay } from './format'
import { needsYou, TAX_NOTICE, TAX_OFF, TaxView } from './TaxView'

/** The tax core's own renderer fixture (one copy, read from there), changed for one test. */
function pack(change: (pack: TaxPack) => void = () => {}): TaxPack {
  const path = join(process.cwd(), '..', 'sudarshan', 'tax', 'tests', 'vectors', 'pack.json')
  const out = JSON.parse(readFileSync(path, 'utf8'))[0].out as TaxPack
  change(out)
  return out
}

const INPUTS = {
  member_id: 1,
  accounts: [],
  opening_lots: [],
  fmvs: [],
  carries: [],
  day_overrides: [],
  trade_marks: [],
  settings: {},
}
const OWNER: Member = { id: 1, name: 'Owner', relation: null, version: 1 }

function show(answer: TaxPack | PortfolioError, members: Member[] = [OWNER]) {
  api.call.mockImplementation(async (op: string) => {
    if (op === 'tax_inputs') return { enabled: true, data: INPUTS }
    if (op !== 'tax_get') throw new Error(`unexpected op ${op}`)
    if (answer instanceof PortfolioError) throw answer
    return { enabled: true, data: answer }
  })
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={['/portfolio?view=tax']}>
        <TaxView members={members} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  api.call.mockReset()
})

describe('the words and times', () => {
  it('names a financial year and finds the one a day falls in', () => {
    expect(fyLabel(2026)).toBe('FY 2026-27')
    expect(fyLabel(1999)).toBe('FY 1999-00')
    expect(fyOfDay(20260331)).toBe(2025)
    expect(fyOfDay(20260401)).toBe(2026)
  })

  it('says when the paper was worked out and how old the read is, and calls an old read old', () => {
    const p = pack()
    const fresh = asOf(p, p.as_of_ms)
    expect(fresh.words).toBe('As of 14 Nov 2026, 22:00 · Year to date · read from Upstox 1 h ago')
    expect(fresh.old).toBe(false)
    expect(asOf(p, p.as_of_ms + 3 * 86_400_000).old).toBe(true)
    expect(
      asOf(
        pack((x) => {
          x.running = false
        }),
        p.as_of_ms + 9e9
      ).old
    ).toBe(false)
  })

  it('counts what waits for the owner and keeps the service’s own lines', () => {
    const p = pack((x) => {
      x.capital_gains.needs_fmv = [x.capital_gains.schedule[0], x.capital_gains.schedule[1]]
      x.capital_gains.unknown_cost = [x.capital_gains.schedule[0]]
      x.inputs.missing = ['Upstox’s F&O trades of FY 2026-27 are not read in full yet.']
    })
    expect(needsYou(p)).toEqual([
      '2 sales need the 31 Jan 2018 price',
      '1 sale has no purchase record',
      'Upstox’s F&O trades of FY 2026-27 are not read in full yet.',
    ])
    expect(needsYou(pack())).toEqual([])
  })
})

describe('the view', () => {
  it('shows each head’s figures as the pack gives them, with sign and word', async () => {
    show(pack())
    const gains = await screen.findByRole('region', { name: 'Capital gains' })
    expect(within(gains).getAllByText('₹24,966.53').length).toBeGreaterThan(0) // taxable
    expect(within(gains).getAllByText('₹4,993.31')).toHaveLength(2) // the tax, and its short-term line
    expect(within(gains).getByText(/₹199.73 cess at 4%, before surcharge/)).toBeTruthy()
    expect(within(gains).getByText('of ₹1,25,000.00 a year')).toBeTruthy()
    expect(within(gains).getByText('Long-term capital loss of FY 2026-27')).toBeTruthy()
    expect(within(gains).getByText('Usable up to FY 2034-35')).toBeTruthy()
    const business = screen.getByRole('region', { name: 'Business income' })
    expect(within(business).getByText('F&O and other derivatives')).toBeTruthy()
    expect(within(business).getByText('Sum of profits and losses, ICAI method')).toBeTruthy()
    expect(within(business).getAllByText('Gain').length).toBeGreaterThan(0)
    expect(within(business).getByText(/1 position is still open/)).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Needs you' })).toBeNull()
    // Dividends wait for their own switch, and say so in the card's own sentence.
    expect(
      within(screen.getByRole('region', { name: 'Dividends' })).getByText(DIVIDENDS_OFF)
    ).toBeTruthy()
  })

  it('keeps the notice, says the age of the figures and never says Real-time', async () => {
    show(pack())
    expect(screen.getByRole('note').textContent).toBe(TAX_NOTICE)
    expect(await screen.findByText(/^As of 14 Nov 2026, 22:00 · Year to date/)).toBeTruthy()
    expect(screen.getByRole('note').className).toContain('sticky')
    expect(document.body.textContent).not.toMatch(/Real-time|Live/)
  })

  it('opens the year’s sales, each with its term chip and its gain or loss in words', async () => {
    show(pack())
    await userEvent.click(
      await screen.findByRole('button', { name: /Every sale of the year \(3\)/ })
    )
    const sheet = await screen.findByRole('dialog')
    expect(within(sheet).getAllByRole('listitem')).toHaveLength(3)
    expect(within(sheet).getByText('−₹30,066.93')).toBeTruthy()
    expect(within(sheet).getAllByText('Loss')).toHaveLength(1)
    expect(within(sheet).getAllByText('Long-term')).toHaveLength(2)
    expect(within(sheet).getByText('Buyback')).toBeTruthy()
    expect(within(sheet).getAllByText(/\(allocated\)/)).toHaveLength(2)
  })

  it('names each sale by its share with the ISIN under it, and by the ISIN alone when unnamed', async () => {
    show(
      pack((x) => {
        x.capital_gains.schedule[0].name = 'Invented Industries Ltd'
        x.capital_gains.schedule[2].name = null
      })
    )
    await userEvent.click(
      await screen.findByRole('button', { name: /Every sale of the year \(3\)/ })
    )
    const [first, , third] = within(await screen.findByRole('dialog')).getAllByRole('listitem')
    const name = within(first).getByText('Invented Industries Ltd')
    expect(name.className).toContain('font-semibold')
    expect(within(first).getByText('INE000A01010').className).toContain('text-xs')
    // No record names the third: its ISIN stands in the name's place, once.
    expect(within(third).getAllByText(/^INE/)).toHaveLength(1)
    expect(within(third).getByText(/^INE/).className).toContain('font-semibold')
  })

  it('lists a sale left out of the totals with its reason', async () => {
    show(
      pack((x) => {
        const sale = x.capital_gains.schedule[0]
        x.capital_gains.needs_fmv = [{ ...sale, status: 'needs_fmv', gain_paise: null }]
      })
    )
    expect(await screen.findByText('1 sale needs the 31 Jan 2018 price')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /Every sale of the year \(4\)/ }))
    expect(await screen.findByText(/Needs the 31 Jan 2018 price/)).toBeTruthy()
    expect(screen.getByText('Not worked out')).toBeTruthy()
  })

  it('keeps a large figure whole and its row unbroken', async () => {
    show(
      pack((x) => {
        x.capital_gains.st_gain_paise = 12_34_56_78_901_23
      })
    )
    const figure = await screen.findByText('₹12,34,56,78,901.23')
    expect(figure.className).toContain('whitespace-nowrap')
    expect(figure.className).toContain('shrink-0')
  })

  it('offers the three years Upstox keeps, the year running first, and asks for the one chosen', async () => {
    vi.useFakeTimers({ toFake: ['Date'] }).setSystemTime(new Date('2026-11-14T12:00:00+05:30'))
    show(pack())
    const years = within(await screen.findByRole('group', { name: 'Financial year' }))
    expect(years.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'FY 2026-27',
      'FY 2025-26',
      'FY 2024-25',
    ])
    expect(screen.queryByRole('group', { name: 'Taxpayer' })).toBeNull()
    await userEvent.click(years.getByRole('button', { name: 'FY 2025-26' }))
    expect(api.call).toHaveBeenLastCalledWith(
      'tax_get',
      { fy: 2025, member: undefined },
      expect.anything()
    )
    for (const button of years.getAllByRole('button'))
      expect(button.className).toContain('min-h-12')
    vi.useRealTimers()
  })

  it('offers the taxpayers when there is more than one', async () => {
    show(pack(), [OWNER, { ...OWNER, id: 2, name: 'Second' }])
    const who = within(await screen.findByRole('group', { name: 'Taxpayer' }))
    await userEvent.click(who.getByRole('button', { name: 'Second' }))
    expect(api.call.mock.lastCall?.[1]).toMatchObject({ member: 2 })
  })

  it('says its own sentence while the switch is off, and the service’s sentence otherwise', async () => {
    const off = new PortfolioError({ code: 'not_ready', message: 'not ready', entryId: null })
    const { unmount } = show(off)
    expect(await screen.findByText(TAX_OFF)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getByRole('note').textContent).toBe(TAX_NOTICE)
    unmount()
    show(
      new PortfolioError({
        code: 'bad_request',
        message: 'Upstox keeps the last three financial years only.',
        entryId: null,
      })
    )
    expect(
      await screen.findByText('Upstox keeps the last three financial years only.')
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })

  it('opens the inputs from the header and from what needs the owner, and Back closes the sheet', async () => {
    show(
      pack((x) => {
        x.inputs.missing = ['Losses from earlier years may be missing.']
      })
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Open Inputs' }))
    const sheet = await screen.findByRole('dialog', { name: 'Inputs' })
    expect(await within(sheet).findByText('Losses brought forward')).toBeTruthy()
    await userEvent.click(within(sheet).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByRole('button', { name: 'Inputs' }).className).toContain('min-h-12')
  })
})
