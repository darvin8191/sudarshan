/**
 * The trade's sheet in jsdom, the bridge a stand-in. Pinned: what happened (fills, averages, gross,
 * charges est. until actual, net, hold, MAE/MFE with times and capture, latency), the words every
 * chip has, R as typed, Save sends once with the version and only what changed, a conflict is said
 * in one sentence and nothing is saved, and a tap just after the sheet opens does nothing.
 */
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ data: vi.fn() }))
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  journalData: api.data,
}))

import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import { renderBook, where } from '@/test/bookPages'
import { JournalError, WORDS } from './api'
import { closedTrip } from './fixtures'
import { EXPIRED_WORDS, ORPHAN_FILLS, ORPHAN_WORDS, TripSheet, UNMATCHED_WORDS } from './TripSheet'
import type { Playbook, Tag } from './types'

const T0 = Date.UTC(2026, 10, 13, 4, 30, 15) // 10:00:15 IST

function detail(over: Record<string, unknown> = {}) {
  return {
    ...closedTrip({
      id: 7,
      net_paise: 81250,
      close_day: 20261113,
      version: 3,
      strategy: 'ORB',
      hold_s: 14 * 60,
      qty_max: 65,
      entry_avg_u: 1_000_000,
      exit_avg_u: 1_020_000,
      gross_paise: 85299,
      charges_est_paise: 4049,
      mae_paise: -19500,
      mae_ms: T0 + 4 * 60_000,
      mfe_paise: 97500,
      mfe_ms: T0 + 15 * 60_000,
      excursion_source: 'recording',
    }),
    fills: [
      {
        trade_id: 'TEST-1',
        order_id: 'ORD-1',
        mode: 'live',
        day: 20261113,
        exchange: 'NSE',
        symbol: 'SBIN',
        instrument_key: 'NSE_EQ|TEST0000001',
        product: 'MIS',
        action: 'BUY',
        qty: 65,
        price_u: 1_000_000,
        exch_ts_ms: T0,
        order_ts_ms: null,
        source: 'stream_read',
        surface: 'api',
      },
      {
        trade_id: 'TEST-2',
        order_id: 'ORD-2',
        mode: 'live',
        day: 20261113,
        exchange: 'NSE',
        symbol: 'SBIN',
        instrument_key: 'NSE_EQ|TEST0000001',
        product: 'MIS',
        action: 'SELL',
        qty: 65,
        price_u: 1_020_000,
        exch_ts_ms: T0 + 14 * 60_000,
        order_ts_ms: null,
        source: 'close_reconcile',
        surface: 'api',
      },
    ],
    note: {
      round_trip_id: 7,
      notes: 'Waited for the retest.',
      mistakes: [1],
      checklist: '',
      mood: 'calm',
      version: 3,
    },
    latency_ms: 412,
    ...over,
  }
}

const TAGS: Tag[] = [
  { id: 1, kind: 'mistake', name: 'Chased entry', colour: 'gold', archived: false, version: 1 },
  { id: 2, kind: 'mistake', name: 'Moved my stop', colour: 'loss', archived: false, version: 1 },
]
const PLAYBOOKS: Playbook[] = [
  {
    id: 4,
    name: 'Opening range',
    name_alt: null,
    description: '',
    rules: ['Waited for the retest', 'Stop placed'],
    default_risk_paise: 50000,
    archived: false,
    version: 1,
  },
]

function serve(trip = detail()) {
  api.data.mockImplementation(async (op: string) => {
    if (op === 'trip_get') return trip
    if (op === 'tags_list') return TAGS
    if (op === 'playbooks_list') return PLAYBOOKS
    if (op === 'trip_update') return { id: 7, version: 4, words: null }
    throw new Error(`unexpected op ${op}`)
  })
}

const onClose = vi.fn()
const show = () => renderBook('/journal', <TripSheet tradeId={7} open onClose={onClose} />)
const updates = () => api.data.mock.calls.filter(([op]) => op === 'trip_update')

beforeEach(() => {
  api.data.mockReset()
  onClose.mockReset()
  OPEN_GUARD.ms = 0
})

describe('what happened', () => {
  it('shows the fills, the averages, gross, charges as an estimate, net and hold', async () => {
    serve()
    show()
    const result = await screen.findByRole('region', { name: 'Result' })
    expect(within(result).getByText('100.00')).toBeTruthy()
    expect(within(result).getByText('102.00')).toBeTruthy()
    expect(within(result).getByText('+₹852.99')).toBeTruthy()
    const charges = within(result).getByText('Charges').nextElementSibling
    expect(charges?.textContent).toContain('₹40.49')
    expect(charges?.textContent).toContain('est.')
    const net = within(result).getByText('Net').nextElementSibling
    expect(net?.textContent).toContain('+₹812.50')
    expect(net?.textContent).toContain('est.')
    expect(within(result).getByText('14 min')).toBeTruthy()
    const fills = await screen.findByRole('table', { name: 'Fills' })
    const rows = within(fills).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('10:00:15')
    expect(rows[0].textContent).toContain('BUY')
    expect(rows[0].textContent).toContain('Order stream')
    expect(rows[1].textContent).toContain('SELL')
    expect(rows[1].textContent).toContain('1,020.00'.replace('1,020.00', '102.00'))
    expect(rows[1].textContent).toContain('Close check')
  })

  it('says actual once the charges are known', async () => {
    serve(detail({ charges_actual_paise: 4100 }))
    show()
    const result = await screen.findByRole('region', { name: 'Result' })
    const charges = within(result).getByText('Charges').nextElementSibling
    expect(charges?.textContent).toContain('₹41.00')
    expect(charges?.textContent).toContain('actual')
    expect(charges?.textContent).not.toContain('est.')
  })

  it('shows the worst and best points with their times and what was kept of the best', async () => {
    serve()
    show()
    const far = await screen.findByRole('region', { name: 'How far it went' })
    expect(far.textContent).toContain('−₹195.00')
    expect(far.textContent).toContain('at 10:04:15')
    expect(far.textContent).toContain('+₹975.00')
    expect(far.textContent).toContain('at 10:15:15')
    expect(far.textContent).toContain('83.33%')
  })

  it('says why there is no excursion, in the service’s words', async () => {
    serve(
      detail({
        mae_paise: null,
        mfe_paise: null,
        excursion_note: 'The price path was not recorded.',
      })
    )
    show()
    expect(await screen.findByText('The price path was not recorded.')).toBeTruthy()
  })

  it('says how fast the first entry order was answered, as the service measures it', async () => {
    serve()
    show()
    expect(await screen.findByText('answered in 412 ms')).toBeTruthy()
    expect(screen.getByText('First entry order')).toBeTruthy()
    expect(screen.queryByText(/order answered/)).toBeNull()
  })

  it('says an earlier record kept for the notes is one, with no fills and nothing still open', async () => {
    serve(detail({ status: 'orphan', fills: [], exit_avg_u: null, hold_s: null }))
    show()
    expect(await screen.findByText(ORPHAN_WORDS)).toBeTruthy()
    expect(screen.getByText('Earlier record, kept for your notes')).toBeTruthy()
    expect(screen.getByText(ORPHAN_FILLS)).toBeTruthy()
    expect(screen.queryByText('Still open')).toBeNull()
    expect(screen.queryByText(/not available yet/)).toBeNull()
    expect(screen.queryByText('Closed trade')).toBeNull()
  })

  it('says a position opened before the journal is one: no side, no price it opened at, no figure', async () => {
    serve(
      detail({
        status: 'unmatched',
        side: 'long',
        entry_qty: 0,
        entry_avg_u: 0,
        exit_qty: 65,
        gross_paise: null,
        net_paise: null,
        hold_s: null,
        mae_paise: null,
        mfe_paise: null,
        fills: [detail().fills[1]],
      })
    )
    show()
    expect(await screen.findByText(UNMATCHED_WORDS)).toBeTruthy()
    expect(screen.getByText('Opened before the journal started')).toBeTruthy()
    expect(screen.queryByText('LONG')).toBeNull()
    expect(screen.queryByText('SHORT')).toBeNull()
    const result = screen.getByRole('region', { name: 'Result' })
    expect(within(result).getByText('Entry average').nextElementSibling?.textContent).toBe(
      'Not known'
    )
    expect(within(result).getByText('Net').nextElementSibling?.textContent).toBe('Not known')
    expect(within(result).queryByText('0.00')).toBeNull()
    expect(screen.queryByRole('region', { name: 'How far it went' })).toBeNull()
  })

  it('says a contract that ended at expiry did, and works out no result for it', async () => {
    serve(
      detail({
        status: 'expired',
        exit_qty: 0,
        exit_avg_u: null,
        closed_ms: null,
        close_day: 20261118,
        gross_paise: null,
        net_paise: null,
        hold_s: null,
        fills: [detail().fills[0]],
      })
    )
    show()
    expect(await screen.findByText(EXPIRED_WORDS)).toBeTruthy()
    expect(screen.getByText('Ended at expiry', { selector: 'p' })).toBeTruthy()
    const result = screen.getByRole('region', { name: 'Result' })
    expect(within(result).getByText('Exit average').nextElementSibling?.textContent).toBe(
      'Ended at expiry'
    )
    expect(within(result).getByText('Net').nextElementSibling?.textContent).toBe('Not worked out')
    expect(within(result).getByText('Quantity').nextElementSibling?.textContent).toBe('65')
    expect(screen.queryByText('Still open')).toBeNull()
  })

  it('says a Sandbox trade is one', async () => {
    serve(detail({ mode: 'sandbox' }))
    show()
    expect(await screen.findByText('Sandbox trade')).toBeTruthy()
  })

  it('links to the chart of the instrument', async () => {
    serve()
    show()
    const link = await screen.findByRole('link', { name: 'See on chart' })
    expect(link.getAttribute('href')).toBe('/chart/NSE/SBIN')
  })

  it('says when the fills are not there yet', async () => {
    serve(detail({ fills: undefined }))
    show()
    expect(await screen.findByText(/The fills of this trade are not available yet/)).toBeTruthy()
  })
})

describe('what the owner makes of it', () => {
  it('offers six moods by word, a mistake tag by icon and word, and marks the ones on with a tick', async () => {
    serve()
    show()
    const moods = await screen.findByRole('list', { name: 'Mood' })
    expect(
      within(moods)
        .getAllByRole('button')
        .map((b) => b.textContent)
    ).toEqual(['Planned', 'Calm', 'Rushed', 'FOMO', 'Revenge', 'Tired'])
    expect(within(moods).getByRole('button', { name: 'Calm' }).getAttribute('aria-pressed')).toBe(
      'true'
    )
    const tags = screen.getByRole('list', { name: 'Mistake tags' })
    const chased = within(tags).getByRole('button', { name: 'Chased entry' })
    expect(chased.getAttribute('aria-pressed')).toBe('true')
    expect(chased.querySelector('svg')).toBeTruthy()
    expect(
      within(tags).getByRole('button', { name: 'Moved my stop' }).getAttribute('aria-pressed')
    ).toBe('false')
  })

  it('shows R as the risk is typed', async () => {
    const user = userEvent.setup()
    serve()
    show()
    expect(screen.queryByTestId('r-multiple')).toBeNull()
    await user.type(await screen.findByLabelText(/Risk in rupees/), '500')
    expect(screen.getByTestId('r-multiple').textContent).toBe('R +1.63')
    await user.clear(screen.getByLabelText(/Risk in rupees/))
    await user.type(screen.getByLabelText(/Risk in rupees/), '12.345')
    expect(screen.getByText(/rupee amount above zero/)).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: /Save|No changes/ }) as HTMLButtonElement).disabled
    ).toBe(true)
  })

  it('offers a playbook’s rules as yes or no, and its default risk', async () => {
    const user = userEvent.setup()
    serve()
    show()
    await user.selectOptions(await screen.findByLabelText('Playbook'), '4')
    const list = screen.getByRole('region', { name: 'Checklist' })
    expect(within(list).getAllByText(/Waited for the retest|Stop placed/)).toHaveLength(2)
    await user.click(within(list).getAllByRole('button', { name: 'Yes' })[0])
    expect((screen.getByLabelText(/Risk in rupees/) as HTMLInputElement).value).toBe('500')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(updates()).toHaveLength(1))
    expect(updates()[0][1]).toEqual({
      id: 7,
      version: 3,
      setup_id: 4,
      risk_paise: 50000,
      checklist: JSON.stringify([
        { rule: 'Waited for the retest', yes: true },
        { rule: 'Stop placed', yes: false },
      ]),
    })
  })

  it('stops a strategy name at 40 characters with a sentence', async () => {
    const user = userEvent.setup()
    serve()
    show()
    const field = await screen.findByLabelText(/^Strategy \(up to/)
    await user.clear(field)
    await user.type(field, 'x'.repeat(41))
    expect(
      screen.getByText('The strategy name can have up to 40 characters. Shorten it.')
    ).toBeTruthy()
  })
})

describe('Save', () => {
  it('has nothing to save until something changes', async () => {
    serve()
    show()
    const button = await screen.findByRole('button', { name: 'No changes to save' })
    expect((button as HTMLButtonElement).disabled).toBe(true)
  })

  it('sends once with the version and only what changed, however it is tapped, then closes', async () => {
    const user = userEvent.setup()
    serve()
    show()
    const notes = await screen.findByRole('textbox', { name: 'Notes' })
    await user.clear(notes)
    await user.type(notes, 'Sold into strength.')
    await user.click(screen.getByRole('button', { name: 'Rushed' }))
    const save = screen.getByRole('button', { name: 'Save' })
    await act(async () => {
      save.click()
      save.click()
      save.click()
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(updates()).toHaveLength(1)
    expect(updates()[0][1]).toEqual({
      id: 7,
      version: 3,
      notes: 'Sold into strength.',
      mood: 'rushed',
    })
  })

  it('says a conflict in one sentence, saves nothing, keeps what was typed and offers to open it again', async () => {
    const user = userEvent.setup()
    serve()
    show()
    const notes = await screen.findByRole('textbox', { name: 'Notes' })
    await user.type(notes, ' More.')
    api.data.mockImplementation(async (op: string) => {
      if (op === 'trip_update')
        throw new JournalError({ code: 'conflict', message: WORDS.conflict })
      if (op === 'trip_get') return detail({ version: 4 })
      if (op === 'tags_list') return TAGS
      return PLAYBOOKS
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(
      await screen.findByText(
        'This trade’s notes were changed on another screen. Nothing was saved; open it again.'
      )
    ).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
    expect((screen.getByRole('textbox', { name: 'Notes' }) as HTMLTextAreaElement).value).toContain(
      'More.'
    )
    await user.click(screen.getByRole('button', { name: 'Open it again' }))
    await waitFor(() =>
      expect((screen.getByRole('textbox', { name: 'Notes' }) as HTMLTextAreaElement).value).toBe(
        'Waited for the retest.'
      )
    )
    expect(screen.queryByText(/Nothing was saved/)).toBeNull()
  })

  it('adds a mistake tag by name and switches it on for this trade', async () => {
    const user = userEvent.setup()
    serve()
    api.data.mockImplementation(async (op: string, args?: unknown) => {
      if (op === 'trip_get') return detail()
      if (op === 'tags_list') return TAGS
      if (op === 'playbooks_list') return PLAYBOOKS
      if (op === 'tag_create') return { id: 9, version: 1, words: null, args }
      throw new Error(op)
    })
    show()
    await user.type(await screen.findByLabelText('New mistake tag'), 'Oversized')
    await user.click(screen.getByRole('button', { name: 'Add tag' }))
    await waitFor(() => expect(api.data.mock.calls.some(([op]) => op === 'tag_create')).toBe(true))
    const [, args] = api.data.mock.calls.find(([op]) => op === 'tag_create') ?? []
    expect(args).toMatchObject({ kind: 'mistake', name: 'Oversized' })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(updates()).toHaveLength(1))
    expect(updates()[0][1].mistakes).toEqual([1, 9])
  })
})

describe('the sheet itself', () => {
  it('says why it could not read the trade, with the next step', async () => {
    api.data.mockRejectedValue(
      new JournalError({ code: 'unavailable', message: WORDS.unavailable })
    )
    show()
    expect(await screen.findByText(WORDS.unavailable)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })

  it('ignores a tap for half a second after it opens', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    OPEN_GUARD.ms = 500
    try {
      serve()
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      show()
      const rushed = await screen.findByRole('button', { name: 'Rushed' })
      await user.click(rushed)
      expect(rushed.getAttribute('aria-pressed')).toBe('false')
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600)
      })
      await user.click(rushed)
      expect(rushed.getAttribute('aria-pressed')).toBe('true')
    } finally {
      vi.useRealTimers()
      OPEN_GUARD.ms = 0
    }
  })

  it('stays on the page when it is shown', async () => {
    serve()
    show()
    await screen.findByRole('region', { name: 'Result' })
    expect(where()).toBe('/journal')
  })
})
