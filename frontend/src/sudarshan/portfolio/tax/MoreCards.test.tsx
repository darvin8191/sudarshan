/**
 * The rest of the Tax view (P7 F1): the set-off in the service's sentences, Upstox's charges per
 * segment, what the paper leaves out, the reads the year rests on (and one tap to read the rest),
 * and the CSV of each schedule.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn(), save: vi.fn() }))
vi.mock('../api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../api')>()
  return {
    ...real,
    portfolioCall: api.call,
    getTaxCsv: (args: object) => api.call('tax_csv', args),
    saveDownload: api.save,
  }
})

import { PortfolioError } from '../api'
import { REPORT_STATES, type TaxPack, type TaxSource } from '../types'
import { ChargesCard, Downloads, LeftOutCard, LossesCard, ReadsCard, readingOf } from './MoreCards'

function pack(change: (pack: TaxPack) => void = () => {}): TaxPack {
  const path = join(process.cwd(), '..', 'sudarshan', 'tax', 'tests', 'vectors', 'pack.json')
  const out = JSON.parse(readFileSync(path, 'utf8'))[0].out as TaxPack
  change(out)
  return out
}
const source = (segment: TaxSource['segment'], state: TaxSource['state']): TaxSource => ({
  kind: 'trades',
  fy: 2026,
  segment,
  state,
  read_at: null,
})

beforeEach(() => {
  api.call.mockReset()
  api.save.mockReset()
})

describe('the cards', () => {
  it('losses are the service’s own sentences, or say there were none', () => {
    const p = pack()
    const { unmount } = render(<LossesCard pack={p} />)
    for (const line of p.capital_gains.set_off_words) expect(screen.getByText(line)).toBeTruthy()
    unmount()
    render(
      <LossesCard
        pack={pack((x) => {
          x.capital_gains.set_off_words = []
        })}
      />
    )
    expect(screen.getByText('No loss was set off or carried in this year.')).toBeTruthy()
  })

  it('charges are Upstox’s totals per segment, and a year not read says where to read it', () => {
    const { unmount } = render(<ChargesCard pack={pack()} />)
    expect(screen.getByText('Shares')).toBeTruthy()
    expect(screen.getByText('₹300.00')).toBeTruthy()
    expect(screen.getByText('of which STT ₹210.00')).toBeTruthy()
    // The fixture's year is read in full (state `ok`, as the reader sends it): nothing is waiting.
    expect(screen.queryByText(/not read yet/)).toBeNull()
    unmount()
    render(
      <ChargesCard
        pack={pack((x) => {
          x.charges = { by_segment: {}, state: 'partial' }
        })}
      />
    )
    expect(screen.getByText(/Open P&L on the Upstox tab to read them/)).toBeTruthy()
    expect(screen.getByText(/a segment you traded in is not read yet/)).toBeTruthy()
  })

  it('what is left out: the fixed list, the notes, and a sale outside the paper with its amount', () => {
    const p = pack((x) => {
      x.not_computed_sales = [
        { instrument: 'Gold coin', day: 20260702, amount_paise: 12_34_56_789_000 },
      ]
    })
    render(<LeftOutCard pack={p} />)
    for (const line of [...p.not_computed, ...p.notes]) expect(screen.getByText(line)).toBeTruthy()
    expect(screen.getByText('₹1,23,45,67,890.00')).toBeTruthy()
    expect(screen.getByText('Sold 2 Jul 2026')).toBeTruthy()
  })
})

describe('the reads the year rests on', () => {
  it('counts the parts done, being read and not read', () => {
    const state = readingOf([
      source('EQ', 'ok'),
      source('FO', 'reading'),
      source('COM', 'unavailable'),
      source('CD', 'partial'),
      { ...source(null, 'ok'), kind: 'ledger' },
    ])
    expect(state).toEqual({ total: 4, done: 1, reading: ['FO'], unread: ['COM', 'CD'] })
  })

  it('the fixture speaks the reader’s own states, so its year reads as done', () => {
    const p = pack()
    const states = [...p.inputs.sources.map((s) => s.state), p.charges.state]
    for (const state of states) expect(REPORT_STATES).toContain(state)
    expect(readingOf(p.inputs.sources)).toEqual({ total: 1, done: 1, reading: [], unread: [] })
    const { container } = render(<ReadsCard pack={p} onRead={() => {}} />)
    expect(container.innerHTML).toBe('')
  })

  it('shows nothing once every part is read', () => {
    const p = pack((x) => {
      x.inputs.sources = [source('EQ', 'ok'), source('FO', 'ok')]
    })
    const { container } = render(<ReadsCard pack={p} onRead={() => {}} />)
    expect(container.innerHTML).toBe('')
  })

  it('names the parts and reads the rest in one tap, one part at a time', async () => {
    const read = vi.fn()
    api.call.mockResolvedValue({ enabled: true, data: {} })
    const p = pack((x) => {
      x.inputs.sources = [source('EQ', 'ok'), source('FO', 'reading'), source('COM', 'unavailable')]
    })
    render(<ReadsCard pack={p} onRead={read} />)
    const card = within(screen.getByRole('region', { name: 'Reading from Upstox' }))
    expect(
      card.getByText(
        'Reading this year from Upstox. 1 of 3 parts done. Being read: F&O. Not read in full: Commodity.'
      )
    ).toBeTruthy()
    const button = card.getByRole('button', { name: 'Read this year' })
    expect(button.className).toContain('min-h-12')
    await userEvent.click(button)
    await waitFor(() => expect(read).toHaveBeenCalled())
    expect(api.call.mock.calls).toEqual([['report_trades', { fy: 2026, segment: 'COM' }]])
  })

  it('says the service’s sentence when a read fails', async () => {
    api.call.mockRejectedValue(
      new PortfolioError({ code: 'unavailable', message: 'Upstox did not answer.', entryId: null })
    )
    const p = pack((x) => {
      x.inputs.sources = [source('EQ', 'unavailable')]
    })
    render(<ReadsCard pack={p} onRead={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Read this year' }))
    expect(await screen.findByText('Upstox did not answer.')).toBeTruthy()
  })
})

describe('the downloads', () => {
  it('asks for the schedule tapped, for this year and taxpayer, and hands the file to the phone', async () => {
    const file = { filename: 'SUDARSHAN_Tax_FY2026-27_losses.csv', text: 'a,b' }
    api.call.mockResolvedValue({ enabled: true, data: file })
    render(<Downloads pack={pack()} />)
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Capital gains',
      'Business income',
      'Losses',
      'Dividends',
    ])
    await userEvent.click(screen.getByRole('button', { name: 'Losses' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledWith(file))
    expect(api.call).toHaveBeenCalledWith('tax_csv', { fy: 2026, member: 1, schedule: 'losses' })
  })
})
