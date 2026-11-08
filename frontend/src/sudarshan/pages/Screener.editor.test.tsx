/**
 * Writing a screen on the Screener page (M9.3): the new-screen editor, the rule builder's cards
 * and sheets, the service's words beside the condition they are about, and Save sent once with
 * the version-conflict and not-confirmed words. The bridge is a stand-in; time is moved by hand.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  type InitialEntry,
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  state: vi.fn(),
  runsSince: vi.fn(),
  create: vi.fn(),
  change: vi.fn(),
  remove: vi.fn(),
  preview: vi.fn(),
  universes: vi.fn(),
}))
vi.mock('@/sudarshan/screener/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/screener/api')>()),
  screenerApi: api,
}))
const toasts = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ toast: toasts, showToast: toasts }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket: null }),
}))

vi.mock('@/sudarshan/watchlist/useWatchlists', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/watchlist/useWatchlists')>()),
  readLists: async () => [
    { id: 4, name: 'Swing', position: 0, items: [] },
    { id: 5, name: 'Intraday', position: 1, items: [] },
  ],
}))

import { createQueryClient } from '@/app/providers'
import { resetScreenActionsForTests } from '@/sudarshan/screener/actions'
import { ScreenerError } from '@/sudarshan/screener/api'
import { resetScreenerPushForTests } from '@/sudarshan/screener/push'
import { WORDS } from '@/sudarshan/screener/types'
import { CHECK_AFTER_MS } from '@/sudarshan/screener/usePreviewCheck'
import { resultRow, SPEC, screenerState, screenRow } from '@/test/screener'
import Screener from './Screener'

vi.setConfig({ testTimeout: 20_000 })

function Where() {
  const location = useLocation()
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>
}
const history = { go: (_delta: number) => {} }
function Nav() {
  const navigate = useNavigate()
  history.go = (delta) => navigate(delta)
  return null
}
function renderPage(entries: InitialEntry[] = ['/elsewhere', '/screener']) {
  const client = createQueryClient()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Nav />
        <Routes>
          <Route path="/screener" element={<Screener />} />
          <Route path="/elsewhere" element={<p>Another page</p>} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return client
}
const where = () => screen.getByTestId('where').textContent
const back = () => act(() => history.go(-1))
async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
}
const clauseCard = (id: string) => document.querySelector(`[data-clause="${id}"]`) as HTMLElement
const preview = {
  label: 'Preview on the 10:00 to 10:15 candle, not saved',
  count: 2,
  first: [resultRow('SBIN'), resultRow('TCS')],
  not_checked: { count: 1, reasons: ['No prices yet for INFY'] },
}

beforeEach(() => {
  resetScreenActionsForTests()
  resetScreenerPushForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  for (const fn of Object.values(toasts)) fn.mockReset()
  api.state.mockResolvedValue(screenerState())
  api.universes.mockResolvedValue({ universes: [] })
  api.runsSince.mockResolvedValue({ runs: [], more: false })
})
afterEach(() => vi.useRealTimers())

describe('the new screen editor', () => {
  it('opens from New with one condition to fill in, and Back leaves it', async () => {
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /New/ }))
    expect(where()).toBe('/screener?new=1')
    expect(screen.getByText('New screen')).toBeTruthy()
    expect(document.querySelectorAll('[data-clause]')).toHaveLength(1)
    back()
    await waitFor(() => expect(where()).toBe('/screener'))
  })

  it('keeps Save off until the screen has a name, then sends it once with one request id', async () => {
    let answer: (row: unknown) => void = () => {}
    api.create.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    renderPage(['/screener?new=1'])
    const save = await screen.findByRole('button', { name: 'Save' })
    expect((save as HTMLButtonElement).disabled).toBe(true)
    await userEvent.type(screen.getByLabelText('Name'), 'My screen')
    await userEvent.click(save)
    await userEvent.click(screen.getByRole('button', { name: 'Saving' }))
    expect(api.create).toHaveBeenCalledTimes(1)
    const sent = api.create.mock.calls[0][0]
    expect(sent.spec.name).toBe('My screen')
    expect(sent.request_id).toMatch(/[0-9a-f-]{36}/)
    await act(async () => answer(screenRow({ id: 5, name: 'My screen' })))
    await waitFor(() => expect(where()).toBe('/screener?screen=5'))
    expect(toasts.success).toHaveBeenCalledWith('Screen saved.')
  })

  it('says Not confirmed when no answer came, and resends under the same request id', async () => {
    api.create.mockRejectedValueOnce(
      new ScreenerError({ code: 'unanswered', message: 'x', field: null })
    )
    api.create.mockResolvedValueOnce(screenRow({ id: 6 }))
    renderPage(['/screener?new=1'])
    await userEvent.type(await screen.findByLabelText('Name'), 'Again')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(WORDS.not_confirmed)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2))
    expect(api.create.mock.calls[1][0].request_id).toBe(api.create.mock.calls[0][0].request_id)
  })

  it('shows a refusal beside the condition it is about, in the server words', async () => {
    api.create.mockRejectedValue(
      new ScreenerError({
        code: 'invalid',
        message: 'A cross compares two lines on the same candles.',
        field: 'c1',
      })
    )
    renderPage(['/screener?new=1'])
    await userEvent.type(await screen.findByLabelText('Name'), 'X')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(
        within(clauseCard('c1')).getByText('A cross compares two lines on the same candles.')
      ).toBeTruthy()
    )
  })
})

describe('the service checks what is written', () => {
  it('checks once the owner has stopped editing, and shows the words beside the condition', async () => {
    api.preview.mockRejectedValue(
      new ScreenerError({
        code: 'invalid',
        message: 'Turn on at least one condition.',
        field: 'c1',
      })
    )
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderPage(['/screener?new=1'])
    await screen.findByLabelText('Name')
    await userEvent.type(screen.getByLabelText('Name'), 'N')
    expect(api.preview).not.toHaveBeenCalled()
    await act(async () => {
      vi.advanceTimersByTime(CHECK_AFTER_MS + 50)
    })
    await settle()
    expect(api.preview).toHaveBeenCalledTimes(1)
    expect(within(clauseCard('c1')).getByText('Turn on at least one condition.')).toBeTruthy()
  })

  it('tests on the last candle: the label, the count, the matches and the reasons', async () => {
    api.preview.mockResolvedValue(preview)
    renderPage(['/screener?new=1'])
    await userEvent.click(await screen.findByRole('button', { name: 'Test on the last candle' }))
    expect(where()).toBe('/screener?new=1&sheet=preview')
    expect(await screen.findByText('Preview on the 10:00 to 10:15 candle, not saved')).toBeTruthy()
    expect(screen.getByText('2 match')).toBeTruthy()
    expect(screen.getByText('SBIN')).toBeTruthy()
    expect(screen.getByText('Not checked (1)')).toBeTruthy()
    expect(screen.getByText('No prices yet for INFY')).toBeTruthy()
    back()
    await waitFor(() => expect(where()).toBe('/screener?new=1'))
  })
})

describe('the rule builder', () => {
  it('switches a condition off with its word and an icon, and says when none is on', async () => {
    renderPage(['/screener?new=1'])
    await screen.findByLabelText('Name')
    const card = clauseCard('c1')
    const toggle = within(card).getByRole('button', { name: 'This condition is checked' })
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    expect(toggle.textContent).toBe('On')
    await userEvent.click(toggle)
    expect(card.getAttribute('data-on')).toBe('false')
    expect(toggle.textContent).toBe('Off')
    expect(toggle.querySelector('svg')).not.toBeNull()
    expect(screen.getByText('Turn on at least one condition.')).toBeTruthy()
  })

  it('opens the operator list over the editor, and Back closes it first', async () => {
    renderPage(['/elsewhere', '/screener'])
    await userEvent.click(await screen.findByRole('button', { name: /New/ }))
    await userEvent.click(within(clauseCard('c1')).getByRole('button', { name: 'Is above' }))
    expect(where()).toBe('/screener?new=1&sheet=operator&c=c1')
    await userEvent.click(screen.getByRole('button', { name: 'Is between' }))
    expect(screen.getByLabelText('From')).toBeTruthy()
    expect(screen.getByLabelText('To')).toBeTruthy()
    back()
    await waitFor(() => expect(where()).toBe('/screener?new=1'))
    // The choice is kept, written into the sentence, and the number on the right is gone.
    expect(
      within(clauseCard('c1')).getByRole('button', { name: 'Is between 30 and 70' })
    ).toBeTruthy()
    expect(within(clauseCard('c1')).queryByLabelText(/Number, second side/)).toBeNull()
  })

  it('adds a condition and a group, refuses a fourth level and says why', async () => {
    renderPage(['/screener?new=1'])
    await screen.findByLabelText('Name')
    await userEvent.click(screen.getByRole('button', { name: 'Add a group' }))
    const groups = () => document.querySelectorAll('[data-group]')
    expect(groups()).toHaveLength(2)
    const inner = () =>
      [...groups()].filter((g) => g.getAttribute('data-level') === '2')[0] as HTMLElement
    await userEvent.click(within(inner()).getByRole('button', { name: 'Add a group' }))
    expect(groups()).toHaveLength(3)
    const third = [...groups()].filter(
      (g) => g.getAttribute('data-level') === '3'
    )[0] as HTMLElement
    const refused = within(third).getByRole('button', { name: 'Add a group' }) as HTMLButtonElement
    expect(refused.disabled).toBe(true)
    expect(within(third).getByText('Groups can go three levels deep.')).toBeTruthy()
    await userEvent.click(within(third).getByRole('button', { name: 'Add a condition' }))
    expect(document.querySelectorAll('[data-clause]')).toHaveLength(4)
  })

  it('asks before it deletes a condition, and duplicates one under a new id', async () => {
    renderPage(['/screener?new=1'])
    await screen.findByLabelText('Name')
    await userEvent.click(
      within(clauseCard('c1')).getByRole('button', { name: 'More for this condition' })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Duplicate' }))
    expect(document.querySelectorAll('[data-clause]')).toHaveLength(2)
    expect(clauseCard('c2')).not.toBeNull()
    await userEvent.click(
      within(clauseCard('c2')).getByRole('button', { name: 'More for this condition' })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(screen.getByText('Delete this condition?')).toBeTruthy()
    expect(document.querySelectorAll('[data-clause]')).toHaveLength(2)
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(document.querySelectorAll('[data-clause]')).toHaveLength(1))
  })

  it('makes every chip and switch at least 48 dp tall', async () => {
    renderPage(['/screener?new=1'])
    await screen.findByLabelText('Name')
    const card = clauseCard('c1')
    for (const button of within(card).getAllByRole('button')) {
      expect(button.className, button.textContent ?? '').toMatch(/min-h-12|w-14|w-12|size-12/)
    }
    expect(within(card).getByLabelText(/Number, second side/).className).toContain('min-h-12')
  })

  it('writes a typed number into the screen and keeps it', async () => {
    api.create.mockResolvedValue(screenRow({ id: 9 }))
    renderPage(['/screener?new=1'])
    await userEvent.type(await screen.findByLabelText('Name'), 'N')
    const number = within(clauseCard('c1')).getByLabelText('Number, second side')
    await userEvent.clear(number)
    await userEvent.type(number, '2500.5')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.create).toHaveBeenCalled())
    expect(api.create.mock.calls[0][0].spec.where.all[0].right).toEqual({ num: '2500.5' })
  })
})

describe('editing a saved screen', () => {
  it('saves with the version it was opened at, and keeps Save off until something changed', async () => {
    api.change.mockResolvedValue(screenRow({ version: 2, name: 'Renamed' }))
    renderPage(['/screener?screen=1&tab=edit'])
    const save = await screen.findByRole('button', { name: 'Save' })
    expect((save as HTMLButtonElement).disabled).toBe(true)
    const name = screen.getByLabelText('Name')
    await userEvent.clear(name)
    await userEvent.type(name, 'Renamed')
    await userEvent.click(save)
    await waitFor(() => expect(api.change).toHaveBeenCalledTimes(1))
    expect(api.change.mock.calls[0][0]).toBe(1)
    expect(api.change.mock.calls[0][1]).toMatchObject({ version: 1, spec: { name: 'Renamed' } })
  })

  it('shows the version-conflict words and reads the screens again on a conflict', async () => {
    api.change.mockRejectedValue(
      new ScreenerError({ code: 'conflict', message: 'raw', field: null })
    )
    renderPage(['/screener?screen=1&tab=edit'])
    const name = await screen.findByLabelText('Name')
    await userEvent.type(name, '!')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(WORDS.conflict)).toBeTruthy()
    await waitFor(() => expect(api.state.mock.calls.length).toBeGreaterThan(1))
  })

  it('stops Save when the screen changed elsewhere while it was being edited', async () => {
    const client = renderPage(['/screener?screen=1&tab=edit'])
    const name = await screen.findByLabelText('Name')
    await userEvent.type(name, '!')
    await act(async () => {
      client.setQueryData(
        ['sudarshan', 'screener', 'state'],
        screenerState([screenRow({ version: 2, name: 'Other', spec: { ...SPEC, name: 'Other' } })])
      )
    })
    expect((await screen.findByRole('alert')).textContent).toContain(WORDS.conflict)
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('takes a newer version in place when the draft was never touched', async () => {
    const client = renderPage(['/screener?screen=1&tab=edit'])
    await screen.findByLabelText('Name')
    await act(async () => {
      client.setQueryData(
        ['sudarshan', 'screener', 'state'],
        screenerState([screenRow({ version: 2, name: 'Other', spec: { ...SPEC, name: 'Other' } })])
      )
    })
    await waitFor(() =>
      expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Other')
    )
    expect(screen.queryByText(WORDS.conflict)).toBeNull()
  })
})

describe('where matches go and when to be told (M9.19)', () => {
  const saved = () => api.change.mock.calls[0][1].spec

  async function openSaved() {
    api.change.mockResolvedValue(screenRow({ version: 2 }))
    renderPage(['/screener?screen=1&tab=edit'])
    await screen.findByLabelText('Name')
  }
  const save = async () => {
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.change).toHaveBeenCalledTimes(1))
  }

  it('says in words where matches go and how alerts are set', async () => {
    await openSaved()
    expect(screen.getByText(/Alerts on · 1\+ matches · your usual channels/)).toBeTruthy()
    expect(screen.getByText(/up to 10 a check/)).toBeTruthy()
  })

  it('picks one of my lists and saves it with the grace and the cap', async () => {
    await openSaved()
    await userEvent.click(screen.getByRole('button', { name: /Add matches to a watchlist/ }))
    expect(where()).toContain('sheet=target')
    await userEvent.click(await screen.findByRole('button', { name: 'One of mine' }))
    await userEvent.selectOptions(screen.getByLabelText('Your list'), '5')
    await userEvent.click(screen.getByRole('button', { name: 'At the end of the day' }))
    const cap = screen.getByLabelText('Most to add in one check')
    await userEvent.clear(cap)
    await userEvent.type(cap, '25')
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(where()).not.toContain('sheet=')
    await save()
    expect(saved().target).toEqual({
      list_id: 5,
      create_name: null,
      grace: { mode: 'day_end' },
      max_adds_per_run: 25,
    })
  })

  it('refuses a number out of range in the service words and keeps the last good value (99, before the third digit)', async () => {
    await openSaved()
    await userEvent.click(screen.getByRole('button', { name: /Add matches to a watchlist/ }))
    const cap = await screen.findByLabelText('Most to add in one check')
    await userEvent.clear(cap)
    await userEvent.type(cap, '999')
    expect(
      await screen.findByText('The most to add in one check must be a whole number from 1 to 250.')
    ).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))
    await save()
    expect(saved().target.max_adds_per_run).toBe(99)
  })

  it('switches the list off, and saves that as no list rather than a missing one', async () => {
    await openSaved()
    await userEvent.click(screen.getByRole('button', { name: /Add matches to a watchlist/ }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'Add matches to a watchlist', pressed: true })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.getByText('Matches are not added to a watchlist.')).toBeTruthy()
    await save()
    expect(saved().target).toBeNull()
  })

  it('sets alerts: where they go, which figures, and warns when they go nowhere', async () => {
    await openSaved()
    await userEvent.click(screen.getByRole('button', { name: /^Alerts/ }))
    expect(where()).toContain('sheet=alerts')
    await userEvent.click(await screen.findByRole('button', { name: 'Telegram' }))
    expect(screen.getByRole('button', { name: 'Telegram' }).getAttribute('aria-pressed')).toBe(
      'true'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Telegram' }))
    await userEvent.click(screen.getByRole('button', { name: 'The app' }))
    expect(await screen.findByText(/Alerts are on but go nowhere/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'The app' }))
    await userEvent.click(screen.getByRole('button', { name: 'Rank value' }))
    await userEvent.click(screen.getByRole('button', { name: 'Pause alerts' }))
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))
    await save()
    expect(saved().alerts).toMatchObject({
      channels: { telegram: false, app: true },
      columns: ['ltp', 'change_pct', 'value'],
      after_trigger: 'pause',
    })
  })

  it('keeps at least one figure in the alert', async () => {
    await openSaved()
    await userEvent.click(screen.getByRole('button', { name: /^Alerts/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Price' }))
    await userEvent.click(screen.getByRole('button', { name: '% change' }))
    expect(screen.getByRole('button', { name: '% change' }).getAttribute('aria-pressed')).toBe(
      'true'
    )
  })

  it('closes a settings sheet on Back, leaving the screen open', async () => {
    await openSaved()
    await userEvent.click(screen.getByRole('button', { name: /^Alerts/ }))
    await screen.findByRole('button', { name: 'Pause alerts' })
    back()
    await waitFor(() => expect(where()).not.toContain('sheet='))
    expect(where()).toContain('screen=1')
  })
})
