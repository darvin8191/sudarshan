/** The Reports page in jsdom, the bridge a stand-in answering as the contract says. */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ data: vi.fn(), call: vi.fn() }))
vi.mock('@/sudarshan/journal/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/journal/api')>()),
  journalData: api.data,
  journalCall: api.call,
}))

import { JournalError } from '@/sudarshan/journal/api'
import type { Report } from '@/sudarshan/journal/types'
import { renderBook } from '@/test/bookPages'
import Reports from './Reports'

const row = (over: Partial<Report> & { reason?: string } = {}): Report => ({
  id: 1,
  kind: 'eod',
  period_key: '20261109',
  built_ms: 1_762_683_900_000,
  status: 'sent',
  text: 'Day report text.',
  file_name: 'x.pdf',
  file_mime: 'application/pdf',
  file_sha256: null,
  reconciled_at: null,
  version: 1,
  ...over,
})
const SETTINGS = {
  capital_base_paise: 50_000_000,
  eod_on: true,
  brief_on: true,
  weekly_on: false,
  monthly_on: true,
  fy_on: true,
  report_language: 'en',
  charges_plan: 'plus',
  version: 3,
  alerts_on: true,
  rate_card: { version: 'Card 2026-04', from_day: 20260401 },
  times: {
    brief: '08:45',
    eod: '15:45',
    check: '16:05',
    weekly: 'Sunday 18:00',
    monthly: 'the 1st at 07:00',
    fy: '1 April 07:00',
  },
}
const NOT_SAID = 'The server has not said the time yet.'

function serve(
  rows: Report[] = [
    row(),
    row({
      id: 2,
      kind: 'weekly',
      period_key: '2026W45',
      built_ms: 1_762_000_000_000,
      status: 'text_only',
      file_name: null,
    }),
  ],
  settings: object = SETTINGS
) {
  api.call.mockResolvedValue({ enabled: true, data: { enabled: true } })
  api.data.mockImplementation(async (op: string, args: object) => {
    if (op === 'reports_list') return rows
    if (op === 'settings_get') return settings
    if (op === 'report_get') return rows.find((r) => r.id === (args as { id: number }).id)
    if (op === 'report_build' || op === 'settings_set') return { done: true }
    throw new Error(`unexpected op ${op}`)
  })
}
const calls = (op: string) => api.data.mock.calls.filter(([name]) => name === op)

beforeEach(() => {
  api.data.mockReset()
  api.call.mockReset()
})

describe('Archive', () => {
  it('lists reports newest first with kind, period, delivery and the two buttons', async () => {
    serve()
    renderBook('/reports', <Reports />)
    const items = await screen.findAllByTestId('report')
    expect(items).toHaveLength(2)
    expect(within(items[0]).getByText('Day report')).toBeTruthy()
    expect(within(items[0]).getByText('Mon 9 Nov 2026')).toBeTruthy()
    expect(within(items[0]).getByTestId('delivery').textContent).toBe('Sent')
    const link = within(items[0]).getByRole('link', { name: /Download Day report/ })
    expect(link.getAttribute('download')).toBe('SUDARSHAN_Day_report_20261109.pdf')
    expect(link.getAttribute('href')).toMatch(/\/reports\/1\/file$/)
    expect(within(items[1]).getByTestId('delivery').textContent).toMatch(/^Text only/)
    expect(within(items[1]).queryByRole('link')).toBeNull()
  })

  it('offers a monthly statement as a CSV too, next to Download, and no other report', async () => {
    serve([
      row({
        id: 5,
        kind: 'monthly',
        period_key: '202610',
        file_name: 'SUDARSHAN_Monthly-statement_202610.pdf',
      }),
      row({ id: 1, built_ms: 1_762_000_000_000 }),
    ])
    renderBook('/reports', <Reports />)
    const items = await screen.findAllByTestId('report')
    const monthly = within(items[0])
    expect(monthly.getByText('Monthly statement')).toBeTruthy()
    const csv = monthly.getByRole('link', {
      name: 'Download CSV of Monthly statement October 2026',
    })
    expect(csv.getAttribute('href')).toMatch(/\/reports\/5\/file\?format=csv$/)
    expect(csv.getAttribute('download')).toBe('SUDARSHAN_Monthly_statement_202610.csv')
    expect(csv.textContent).toBe('Download CSV')
    const pdf = monthly.getByRole('link', { name: 'Download Monthly statement October 2026' })
    expect(pdf.getAttribute('href')).toMatch(/\/reports\/5\/file$/)
    expect(within(items[1]).getByText('Day report')).toBeTruthy()
    expect(within(items[1]).queryByRole('link', { name: /CSV/ })).toBeNull()
  })

  it('says the alerts service is off when the server says so', async () => {
    serve([row({ status: 'not_sent' })], { ...SETTINGS, alerts_on: false })
    renderBook('/reports', <Reports />)
    await waitFor(() =>
      expect(screen.getByTestId('delivery').textContent).toBe(
        'Not sent: the alerts service is switched off on the server'
      )
    )
  })

  it('says what an empty archive means, with the day report’s time as the server says it', async () => {
    serve([])
    renderBook('/reports', <Reports />)
    const empty =
      "No reports yet. The Day report is built at 15:45 on each trading day. You can also build the tax pack or this month's statement above."
    expect(await screen.findByText(empty)).toBeTruthy()
  })

  it('never guesses the time of an empty archive’s first report', async () => {
    const { times: _times, ...older } = SETTINGS
    serve([], older)
    renderBook('/reports', <Reports />)
    const words = await screen.findByText(/No reports yet/)
    await waitFor(() => expect(calls('settings_get').length).toBeGreaterThan(0))
    expect(words.textContent).toContain(
      "The Day report is built after each trading day's close; the server has not said the time yet."
    )
    expect(words.textContent).not.toMatch(/\d\d:\d\d/)
  })

  it('opens the text in a sheet kept in the address, and Back closes it first', async () => {
    const user = userEvent.setup()
    serve()
    renderBook('/reports', <Reports />)
    await user.click((await screen.findAllByRole('button', { name: 'Open' }))[0])
    expect((await screen.findByTestId('report-text')).textContent).toBe('Day report text.')
    expect(screen.getByTestId('where').textContent).toContain('report=1')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByTestId('report-text')).toBeNull())
    expect(screen.getByTestId('where').textContent).not.toContain('report=')
  })

  it('builds the tax pack and the monthly statement on request, once at a time', async () => {
    const user = userEvent.setup()
    serve()
    renderBook('/reports', <Reports />)
    await screen.findAllByTestId('report')
    await user.click(screen.getByRole('button', { name: 'Build the FY pack now' }))
    await waitFor(() => expect(calls('report_build')).toHaveLength(1))
    expect(calls('report_build')[0][1]).toEqual({ kind: 'fy' })
    expect(await screen.findByText(/Asked for the tax pack/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /Build this month.s statement now/ }))
    await waitFor(() => expect(calls('report_build')).toHaveLength(2))
    expect(calls('report_build')[1][1]).toEqual({ kind: 'monthly' })
  })

  it('says why it cannot read, with the next step, and tries again', async () => {
    const user = userEvent.setup()
    api.call.mockResolvedValue({ enabled: true, data: { enabled: true } })
    api.data.mockImplementation(async (op: string) => {
      if (op === 'settings_get') return SETTINGS
      throw new JournalError({
        code: 'unavailable',
        message:
          'The journal service is not answering. Your trades are safe; try again in a minute.',
      })
    })
    renderBook('/reports', <Reports />)
    expect(await screen.findByText(/not answering/)).toBeTruthy()
    serve()
    await user.click(screen.getByRole('button', { name: /Try again/ }))
    expect((await screen.findAllByTestId('report')).length).toBe(2)
  })
})

describe('Settings', () => {
  const settingsTab = async (user: ReturnType<typeof userEvent.setup>) => {
    renderBook('/reports', <Reports />)
    await user.click(await screen.findByRole('tab', { name: 'Settings' }))
  }

  it('shows each switch with its time and rule, capital in en-IN, English and the rate card', async () => {
    const user = userEvent.setup()
    serve()
    await settingsTab(user)
    await screen.findAllByTestId('when')
    expect(screen.getAllByTestId('when').map((line) => line.textContent)).toEqual([
      '15:45, 5 minutes after the F&O close; a special day follows its own close',
      '08:45, 30 minutes before the first session; a special day follows its own session',
      'Sunday 18:00, every week',
      'the 1st at 07:00, for the month just ended',
      '1 April 07:00, for the year just ended; on request any time',
    ])
    expect(
      screen.getByText(
        "Close check at 16:05: the day's fills are read again and the journal corrected."
      )
    ).toBeTruthy()
    expect(
      (screen.getByRole('switch', { name: 'Day report' }) as HTMLElement).getAttribute(
        'aria-checked'
      )
    ).toBe('true')
    expect(
      screen.getByRole('switch', { name: 'Week in review' }).getAttribute('aria-checked')
    ).toBe('false')
    expect((screen.getByLabelText(/Trading capital/) as HTMLInputElement).value).toBe('500000')
    expect(screen.getByText('English only for now.')).toBeTruthy()
    expect(screen.getByText(/Card 2026-04, in force from/)).toBeTruthy()
    expect(screen.getByTestId('where').textContent).toContain('view=settings')
  })

  it('prints the times the server says, 15:50 for the day report from 7 Dec', async () => {
    const user = userEvent.setup()
    serve(undefined, { ...SETTINGS, times: { ...SETTINGS.times, eod: '15:50' } })
    await settingsTab(user)
    const lines = await screen.findAllByTestId('when')
    expect(lines[0].textContent).toBe(
      '15:50, 5 minutes after the F&O close; a special day follows its own close'
    )
    expect(screen.queryByText(/15:45/)).toBeNull()
  })

  it('says the server has not said a time it did not send, and guesses none', async () => {
    const user = userEvent.setup()
    const { times: _times, ...older } = SETTINGS
    serve(undefined, older)
    await settingsTab(user)
    const lines = await screen.findAllByTestId('when')
    expect(lines.map((line) => line.textContent)).toEqual([
      `5 minutes after the F&O close; a special day follows its own close. ${NOT_SAID}`,
      `30 minutes before the first session; a special day follows its own session. ${NOT_SAID}`,
      `Every week. ${NOT_SAID}`,
      `For the month just ended. ${NOT_SAID}`,
      `For the year just ended; on request any time. ${NOT_SAID}`,
    ])
    expect(screen.queryByText(/Close check/)).toBeNull()
    expect(screen.queryByText(/\d\d:\d\d/)).toBeNull()
  })

  it('a day with no session ahead leaves the day times unsaid', async () => {
    const user = userEvent.setup()
    serve(undefined, {
      ...SETTINGS,
      times: { ...SETTINGS.times, brief: null, eod: null, check: null },
    })
    await settingsTab(user)
    const lines = await screen.findAllByTestId('when')
    expect(lines[0].textContent).toBe(
      `5 minutes after the F&O close; a special day follows its own close. ${NOT_SAID}`
    )
    expect(lines[2].textContent).toBe('Sunday 18:00, every week')
    expect(screen.queryByText(/Close check/)).toBeNull()
  })

  it('saves a switch with the version it read', async () => {
    const user = userEvent.setup()
    serve()
    await settingsTab(user)
    await user.click(await screen.findByRole('switch', { name: 'Week in review' }))
    await waitFor(() => expect(calls('settings_set')).toHaveLength(1))
    expect(calls('settings_set')[0][1]).toEqual({ weekly_on: true, version: 3 })
  })

  it('saves capital only when it changed and is a rupee amount', async () => {
    const user = userEvent.setup()
    serve()
    await settingsTab(user)
    const input = (await screen.findByLabelText(/Trading capital/)) as HTMLInputElement
    expect(
      (screen.getByRole('button', { name: 'No changes to save' }) as HTMLButtonElement).disabled
    ).toBe(true)
    await user.clear(input)
    await user.type(input, 'lots')
    expect(screen.getByText(/Correct it or clear it/)).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: /Save capital|No changes/ }) as HTMLButtonElement)
        .disabled
    ).toBe(true)
    await user.clear(input)
    await user.type(input, '7,50,000')
    await user.click(screen.getByRole('button', { name: 'Save capital' }))
    await waitFor(() => expect(calls('settings_set')).toHaveLength(1))
    expect(calls('settings_set')[0][1]).toEqual({ capital_base_paise: 75_000_000, version: 3 })
  })
})

describe('the page', () => {
  it('says the journal is off in one sentence', async () => {
    api.call.mockResolvedValue({
      enabled: false,
      data: null,
      message: 'The journal is switched off on the server.',
    })
    renderBook('/reports', <Reports />)
    expect(await screen.findByText('The journal is switched off on the server.')).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
  })
})
