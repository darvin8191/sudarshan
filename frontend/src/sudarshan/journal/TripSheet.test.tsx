/**
 * The trade's sheet in jsdom, the bridge a stand-in. Pinned: what happened (fills, averages, gross,
 * charges est. until actual, net, hold, MAE/MFE with times and capture, latency), the words every
 * chip has, R as typed, Save sends once with the version and only what changed, a conflict is said
 * in one sentence and nothing is saved, and a tap just after the sheet opens does nothing.
 */
import { onlineManager } from '@tanstack/react-query'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
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
import {
  EXPIRED_WORDS,
  ORPHAN_FILLS,
  ORPHAN_WORDS,
  TripSheet,
  UNMATCHED_WORDS,
  unclosedWords,
} from './TripSheet'
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
const notesField = () => screen.getByRole('textbox', { name: 'Notes' }) as HTMLTextAreaElement

beforeEach(() => {
  api.data.mockReset()
  onClose.mockReset()
  sessionStorage.clear()
  onlineManager.setOnline(true)
})

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

  it('says how fast the first entry order was answered, in en-IN digits (review L1)', async () => {
    serve(detail({ latency_ms: 1412 }))
    show()
    expect(await screen.findByText('answered in 1,412 ms')).toBeTruthy()
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

  it('says a trade the journal did not see closed is one: its side and entry kept, no result (verification N2)', async () => {
    serve(
      detail({
        status: 'unmatched',
        side: 'long',
        entry_qty: 65,
        exit_qty: 0,
        exit_avg_u: null,
        closed_ms: Date.UTC(2026, 10, 19, 18, 30),
        close_day: 20261120,
        gross_paise: null,
        net_paise: null,
        hold_s: null,
        mae_paise: null,
        mfe_paise: null,
        fills: [detail().fills[0]],
      })
    )
    show()
    expect(await screen.findByText(unclosedWords(20261120))).toBeTruthy()
    expect(unclosedWords(20261120)).toContain('When it checked your Upstox holdings on Fri 20 Nov')
    expect(screen.getByText('Closing not seen by the journal')).toBeTruthy()
    expect(screen.queryByText(UNMATCHED_WORDS)).toBeNull()
    expect(screen.getByText('LONG')).toBeTruthy()
    const result = screen.getByRole('region', { name: 'Result' })
    expect(within(result).getByText('Entry average').nextElementSibling?.textContent).not.toBe(
      'Not known'
    )
    expect(within(result).getByText('Exit average').nextElementSibling?.textContent).toBe(
      'Not seen'
    )
    expect(within(result).getByText('Net').nextElementSibling?.textContent).toBe('Not known')
    expect(within(result).getByText('Held').nextElementSibling?.textContent).toBe('-')
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

  it('shows a reversal fill as the part of it that is this trade’s, and where the rest went', async () => {
    const [buy, sell] = detail().fills
    serve(
      detail({
        qty_max: 650,
        fills: [
          { ...buy, qty: 650, trip_qty: 650 },
          { ...sell, qty: 1300, trip_qty: 650 },
        ],
      })
    )
    show()
    const fills = await screen.findByRole('table', { name: 'Fills' })
    const qty = within(fills)
      .getAllByTestId('fill-qty')
      .map((cell) => cell.textContent)
    expect(qty).toEqual(['650', '650 of 1,300'])
    expect(
      screen.getByText('The rest of the 10:14:15 SELL belongs to the next trade.')
    ).toBeTruthy()
  })

  it('says a trade on an exchange the estimate does not cover has no charges counted', async () => {
    serve(detail({ charges_modelled: false, charges_est_paise: 0, gross_paise: 81250 }))
    show()
    const result = await screen.findByRole('region', { name: 'Result' })
    expect(within(result).getByText('Charges').nextElementSibling?.textContent).toBe(
      'Not estimated for this exchange'
    )
    expect(within(result).getByText('Net').nextElementSibling?.textContent).toContain(
      'no charges counted'
    )
    expect(within(result).queryByText('₹0.00')).toBeNull()
  })

  it('says charges rebuilt from Upstox’s history are an estimate from it', async () => {
    const [buy, sell] = detail().fills
    serve(
      detail({
        fills: [
          { ...buy, source: 'history' },
          { ...sell, source: 'history' },
        ],
      })
    )
    show()
    const result = await screen.findByRole('region', { name: 'Result' })
    expect(within(result).getByText('Charges').nextElementSibling?.textContent).toContain(
      'est., from Upstox’s history'
    )
  })

  it('says a Sandbox trade is one', async () => {
    serve(detail({ mode: 'sandbox' }))
    show()
    expect(await screen.findByText('Sandbox trade')).toBeTruthy()
  })

  it('names the contract in its title as the books do, with its exchange and expiry (review H5)', async () => {
    serve(detail({ exchange: 'NFO', symbol: 'NIFTY17NOV2625000CE' }))
    show()
    const title = await screen.findByRole('heading', { name: /NIFTY 25000 CE/ })
    expect(title.textContent).not.toContain('NIFTY17NOV')
    expect(screen.getByText('Closed trade').textContent).toBe('Closed trade · NFO · 17 Nov 2026')
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
    // review M1: the other screen did not touch the notes, so what was typed stays in the field,
    // now on the new version
    await waitFor(() => expect(screen.queryByText(/Nothing was saved/)).toBeNull())
    expect(notesField().value).toBe('Waited for the retest. More.')
    expect(screen.getByText(/This trade was changed on another screen/)).toBeTruthy()
    api.data.mockImplementation(async (op: string) => {
      if (op === 'trip_update') return { id: 7, version: 5, words: null }
      if (op === 'trip_get') return detail({ version: 4 })
      if (op === 'tags_list') return TAGS
      return PLAYBOOKS
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(updates().at(-1)?.[1]).toEqual({
      id: 7,
      version: 4,
      notes: 'Waited for the retest. More.',
    })
  })

  it('says nothing of another screen when the read after its own Save comes back', async () => {
    const user = userEvent.setup()
    let version = 3
    let notes = 'Waited for the retest.'
    api.data.mockImplementation(async (op: string, args?: { notes?: string }) => {
      if (op === 'trip_get') return detail({ version, note: { ...detail().note, notes, version } })
      if (op === 'tags_list') return TAGS
      if (op === 'playbooks_list') return PLAYBOOKS
      if (op === 'trip_update') {
        version = 4
        notes = args?.notes ?? notes
        return { id: 7, version: 4, words: null }
      }
      throw new Error(`unexpected op ${op}`)
    })
    show()
    await user.type(await screen.findByRole('textbox', { name: 'Notes' }), ' Done.')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(screen.queryByText(/changed on another screen/)).toBeNull()
    expect(notesField().value).toBe('Waited for the retest. Done.')
  })

  it('hands back the notes typed here when the other screen changed the notes too (review M1)', async () => {
    const user = userEvent.setup()
    serve()
    show()
    await user.type(await screen.findByRole('textbox', { name: 'Notes' }), ' Mine.')
    await user.click(screen.getByRole('button', { name: 'Rushed' }))
    const theirs = detail({
      version: 4,
      note: { ...detail().note, notes: 'Written on the laptop.', mood: 'tired', version: 4 },
    })
    api.data.mockImplementation(async (op: string) => {
      if (op === 'trip_update')
        throw new JournalError({ code: 'conflict', message: WORDS.conflict })
      if (op === 'trip_get') return theirs
      if (op === 'tags_list') return TAGS
      return PLAYBOOKS
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await user.click(await screen.findByRole('button', { name: 'Open it again' }))
    // nothing saved elsewhere is overwritten unseen, and nothing typed here is lost
    await waitFor(() => expect(notesField().value).toBe('Written on the laptop.'))
    const typed = screen.getByTestId('typed-notes')
    expect(typed.textContent).toContain('Waited for the retest. Mine.')
    expect(screen.getByText(/It also changed the mood/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Tired' }).getAttribute('aria-pressed')).toBe('true')
    await user.click(within(typed).getByRole('button', { name: 'Add it to the notes' }))
    expect(notesField().value).toBe('Written on the laptop.\n\nWaited for the retest. Mine.')
    expect(screen.queryByTestId('typed-notes')).toBeNull()
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

describe('what was typed is never lost (reviews H2 and H3)', () => {
  it('asks before Close shuts a sheet holding changes, and Keep writing goes back to them', async () => {
    const user = userEvent.setup()
    serve()
    show()
    await user.type(await screen.findByRole('textbox', { name: 'Notes' }), ' Unsaved words.')
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).not.toHaveBeenCalled()
    expect(await screen.findByText('Your changes to this trade are not saved.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Keep writing' }))
    expect(screen.queryByText('Your changes to this trade are not saved.')).toBeNull()
    expect(notesField().value).toBe('Waited for the retest. Unsaved words.')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('asks before Escape or a tap outside shuts it too', async () => {
    const user = userEvent.setup()
    serve()
    show()
    await user.type(await screen.findByRole('textbox', { name: 'Notes' }), ' x')
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(await screen.findByText('Your changes to this trade are not saved.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Keep writing' }))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    fireEvent.pointerDown(document.body)
    expect(await screen.findByText('Your changes to this trade are not saved.')).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes at once when nothing was changed', async () => {
    const user = userEvent.setup()
    serve()
    show()
    await screen.findByRole('textbox', { name: 'Notes' })
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Discard changes closes and forgets them; Save from the question sends them and closes', async () => {
    const user = userEvent.setup()
    serve()
    const first = show()
    await user.type(await screen.findByRole('textbox', { name: 'Notes' }), ' Gone.')
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await user.click(await screen.findByRole('button', { name: 'Discard changes' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(updates()).toHaveLength(0)
    expect(sessionStorage.getItem('sd-journal-draft:7')).toBeNull()
    first.unmount()

    onClose.mockReset()
    serve()
    show()
    await user.type(await screen.findByRole('textbox', { name: 'Notes' }), ' Kept.')
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(updates()[0][1]).toEqual({ id: 7, version: 3, notes: 'Waited for the retest. Kept.' })
  })

  it('keeps the changes in this tab, so a sheet opened again brings them back', async () => {
    const user = userEvent.setup()
    serve()
    const first = show()
    await user.type(await screen.findByRole('textbox', { name: 'Notes' }), ' Typed before.')
    // the page left by Back, a reload, the phone stopping the tab: the sheet is gone unasked
    first.unmount()
    show()
    await waitFor(() => expect(notesField().value).toBe('Waited for the retest. Typed before.'))
    expect(screen.getByText(/What you typed on this trade before is back in the form/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy()
  })

  it('keeps the editor when a background re-read fails, and says so (review H3)', async () => {
    serve()
    show()
    await screen.findByRole('textbox', { name: 'Notes' })
    api.data.mockImplementation(async (op: string) => {
      if (op === 'trip_get')
        throw new JournalError({ code: 'unavailable', message: WORDS.unavailable })
      if (op === 'tags_list') return TAGS
      return PLAYBOOKS
    })
    // a new network: the open trade is read again, and that read fails
    await act(async () => {
      onlineManager.setOnline(false)
      onlineManager.setOnline(true)
    })
    expect(await screen.findByText(/What you see is from the last read/)).toBeTruthy()
    expect(notesField().value).toBe('Waited for the retest.')
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })

  it('reads nothing under a form holding changes when the network comes back', async () => {
    const user = userEvent.setup()
    serve()
    show()
    await user.type(await screen.findByRole('textbox', { name: 'Notes' }), ' Held too long.')
    const reads = () => api.data.mock.calls.filter(([op]) => op === 'trip_get').length
    const before = reads()
    await act(async () => {
      onlineManager.setOnline(false)
      onlineManager.setOnline(true)
    })
    expect(reads()).toBe(before)
    expect(notesField().value).toBe('Waited for the retest. Held too long.')
  })

  it('keeps what was typed when the read after a conflict fails', async () => {
    const user = userEvent.setup()
    serve()
    show()
    await user.type(await screen.findByRole('textbox', { name: 'Notes' }), ' Mine.')
    api.data.mockImplementation(async (op: string) => {
      if (op === 'trip_update')
        throw new JournalError({ code: 'conflict', message: WORDS.conflict })
      if (op === 'trip_get')
        throw new JournalError({ code: 'unavailable', message: WORDS.unavailable })
      if (op === 'tags_list') return TAGS
      return PLAYBOOKS
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await user.click(await screen.findByRole('button', { name: 'Open it again' }))
    expect(await screen.findByText(/What you see is from the last read/)).toBeTruthy()
    expect(notesField().value).toBe('Waited for the retest. Mine.')
  })
})
