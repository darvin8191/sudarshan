/** The Playbooks part and its sheet in jsdom, the bridge a stand-in. */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ data: vi.fn() }))
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  journalData: api.data,
}))
const socket = vi.hoisted(() => ({
  on() {
    return this
  },
  off() {
    return this
  },
  connected: true,
}))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))
vi.mock('@/sudarshan/dashboard/MarketCalendar', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/dashboard/MarketCalendar')>()),
  MarketCalendarSource: () => null,
}))

import { renderBook } from '@/test/bookPages'
import { JournalError, WORDS } from './api'
import { analytics, metrics } from './fixtures'
import { PlaybookSheetHost } from './PlaybookSheet'
import { EMPTY_WORDS, Playbooks } from './Playbooks'
import type { Playbook } from './types'

const OPENING: Playbook = {
  id: 4,
  name: 'Opening range',
  name_alt: 'ओपनिंग रेंज',
  description: 'First 15 minutes.',
  rules: ['Waited for the retest', 'Stop placed'],
  default_risk_paise: 50000,
  archived: false,
  version: 2,
}
const OLD: Playbook = {
  ...OPENING,
  id: 5,
  name: 'Old idea',
  name_alt: null,
  archived: true,
  rules: [],
}

function serve(list: Playbook[] = [OPENING, OLD]) {
  api.data.mockImplementation(async (op: string) => {
    if (op === 'playbooks_list') return list
    if (op === 'analytics')
      return analytics({
        by_playbook: [
          {
            name: 'Opening range',
            metrics: metrics({ trips: 4, win_rate: '75.00', expectancy_paise: 12000 }),
          },
        ],
      })
    if (op === 'playbook_update') return { id: 4, version: 3, words: null }
    if (op === 'playbook_create') return { id: 9, version: 1, words: null }
    throw new Error(`unexpected op ${op}`)
  })
}
const calls = (op: string) => api.data.mock.calls.filter(([name]) => name === op)

beforeEach(() => {
  api.data.mockReset()
})

describe('Playbooks', () => {
  it('lists each setup with its checks, default risk and how it has done', async () => {
    serve()
    renderBook('/journal', <Playbooks mode="live" onOpen={vi.fn()} onNew={vi.fn()} />)
    const card = (await screen.findAllByTestId('playbook'))[0]
    expect(within(card).getByText('Opening range')).toBeTruthy()
    expect(within(card).getByText('ओपनिंग रेंज')).toBeTruthy()
    expect(within(card).getByText(/2 checks, default risk ₹500\.00/)).toBeTruthy()
    expect(within(card).getByText('4')).toBeTruthy()
    expect(within(card).getByText('75.00%')).toBeTruthy()
    expect(within(card).getByText('+₹120.00')).toBeTruthy()
    expect(calls('analytics')[0][1]).toEqual({ mode: 'live', period: 'all' })
  })

  it('shows no trades for a setup nothing has used, and keeps archived ones apart', async () => {
    serve()
    renderBook('/journal', <Playbooks mode="sandbox" onOpen={vi.fn()} onNew={vi.fn()} />)
    const archived = await screen.findByRole('region', { name: 'Archived playbooks' })
    expect(within(archived).getByText('Old idea')).toBeTruthy()
    expect(within(archived).getByText('No trades')).toBeTruthy()
    expect(calls('analytics')[0][1].mode).toBe('sandbox')
  })

  it('opens a playbook by its number, and a new one from the button', async () => {
    const user = userEvent.setup()
    const onOpen = vi.fn()
    const onNew = vi.fn()
    serve()
    renderBook('/journal', <Playbooks mode="live" onOpen={onOpen} onNew={onNew} />)
    await user.click((await screen.findAllByTestId('playbook'))[0])
    await user.click(screen.getByRole('button', { name: 'New playbook' }))
    expect(onOpen).toHaveBeenCalledWith(4)
    expect(onNew).toHaveBeenCalled()
  })

  it('says what a playbook is when there are none', async () => {
    serve([])
    renderBook('/journal', <Playbooks mode="live" onOpen={vi.fn()} onNew={vi.fn()} />)
    expect(await screen.findByText(EMPTY_WORDS)).toBeTruthy()
  })

  it('says why it could not read, with the next step, and tries again', async () => {
    const user = userEvent.setup()
    api.data.mockRejectedValue(new Error('x'))
    renderBook('/journal', <Playbooks mode="live" onOpen={vi.fn()} onNew={vi.fn()} />)
    await screen.findByRole('button', { name: /Try again/ })
    serve()
    await user.click(screen.getByRole('button', { name: /Try again/ }))
    expect((await screen.findAllByTestId('playbook')).length).toBeGreaterThan(0)
  })
})

describe('the playbook sheet', () => {
  const onClose = vi.fn()
  beforeEach(() => {
    onClose.mockReset()
  })
  const show = (id: number | null) =>
    renderBook('/journal', <PlaybookSheetHost playbookId={id} onClose={onClose} />)

  it('creates a playbook once, however Create is tapped, with a request id', async () => {
    const user = userEvent.setup()
    serve()
    show(null)
    await user.type(await screen.findByLabelText(/^Name \(up to/), 'Gap fade')
    await user.type(screen.getByLabelText('Check 1'), 'Gap is over 0.5%')
    await user.click(screen.getByRole('button', { name: 'Add a check' }))
    await user.type(screen.getByLabelText('Check 2'), 'Volume is up')
    const create = screen.getByRole('button', { name: 'Create playbook' })
    await act(async () => {
      create.click()
      create.click()
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(calls('playbook_create')).toHaveLength(1)
    expect(calls('playbook_create')[0][1]).toMatchObject({
      name: 'Gap fade',
      rules: ['Gap is over 0.5%', 'Volume is up'],
      default_risk_paise: null,
    })
    expect(typeof calls('playbook_create')[0][1].request_id).toBe('string')
  })

  it('stops at 12 checks and says so for a 13th', async () => {
    const user = userEvent.setup()
    serve([{ ...OPENING, rules: Array.from({ length: 12 }, (_, n) => `Check number ${n + 1}`) }])
    show(4)
    const add = await screen.findByRole('button', { name: 'Add a check' })
    expect((add as HTMLButtonElement).disabled).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Remove check 12' }))
    expect(
      (screen.getByRole('button', { name: 'Add a check' }) as HTMLButtonElement).disabled
    ).toBe(false)
  })

  it('sends only what changed, with the version, then closes', async () => {
    const user = userEvent.setup()
    serve()
    show(4)
    const save = await screen.findByRole('button', { name: 'No changes to save' })
    expect((save as HTMLButtonElement).disabled).toBe(true)
    const name = screen.getByLabelText(/^Name \(up to/)
    await user.clear(name)
    await user.type(name, 'ORB')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(calls('playbook_update')[0][1]).toEqual({ id: 4, version: 2, name: 'ORB' })
  })

  it('names what cannot be saved, and sends nothing', async () => {
    const user = userEvent.setup()
    serve()
    show(4)
    await user.clear(await screen.findByLabelText(/^Name \(up to/))
    expect(screen.getByText('Give the playbook a name.')).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: /Save|No changes/ }) as HTMLButtonElement).disabled
    ).toBe(true)
  })

  it('says a conflict in one sentence, saves nothing and keeps what was typed', async () => {
    const user = userEvent.setup()
    serve()
    show(4)
    const name = await screen.findByLabelText(/^Name \(up to/)
    await user.type(name, ' 2')
    api.data.mockImplementation(async (op: string) => {
      if (op === 'playbook_update')
        throw new JournalError({ code: 'conflict', message: WORDS.changedElsewhere })
      return [OPENING]
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(
      await screen.findByText(
        'This playbook was changed on another screen. Nothing was saved; open it again.'
      )
    ).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
    expect((screen.getByLabelText(/^Name \(up to/) as HTMLInputElement).value).toBe(
      'Opening range 2'
    )
    expect(screen.getByRole('button', { name: 'Open it again' })).toBeTruthy()
  })

  it('archives, and brings back, with the version, and says trades keep it', async () => {
    const user = userEvent.setup()
    serve()
    show(4)
    expect(await screen.findByText(/Trades that used it keep it/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Archive this playbook' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(calls('playbook_update')[0][1]).toEqual({ id: 4, version: 2, archived: true })
  })

  it('brings an archived one back', async () => {
    const user = userEvent.setup()
    serve()
    show(5)
    await user.click(await screen.findByRole('button', { name: 'Bring it back' }))
    await waitFor(() => expect(calls('playbook_update')).toHaveLength(1))
    expect(calls('playbook_update')[0][1]).toEqual({ id: 5, version: 2, archived: false })
  })

  it('says a playbook that is gone is gone, and a list that will not read in words', async () => {
    serve([OPENING])
    const first = show(77)
    expect(
      await screen.findByText('This playbook is not there any more. Close this and pick another.')
    ).toBeTruthy()
    first.unmount()
    api.data.mockRejectedValue(
      new JournalError({ code: 'unavailable', message: WORDS.unavailable })
    )
    show(4)
    expect(await screen.findByText(WORDS.unavailable)).toBeTruthy()
    fireEvent.keyDown(document.body, { key: 'Escape' })
  })
})
