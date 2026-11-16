/**
 * The screener's leftovers on the phone (gap audit GA7.4 to GA7.6): the not-checked instruments
 * as groups with their reason and names, a run's results as a file, and the alerts not looked at,
 * counted on a card and cleared when the screen is opened.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, renderHook, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ runCsv: vi.fn(), alertsSeen: vi.fn() }))
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  screenerApi: api,
}))
const saved = vi.hoisted(() => ({ save: vi.fn() }))
vi.mock('@/sudarshan/portfolio/api', () => ({ saveDownload: saved.save }))

import { createQueryClient } from '@/app/providers'
import { screenerState, screenRow } from '@/test/screener'
import { markAlertsSeen, useAlertsSeen } from './alertsSeen'
import { ScreenerError } from './api'
import { STATE_KEY, TODAY_KEY } from './keys'
import { groupNames, NotChecked } from './Results'
import { RunCsv } from './RunCsv'
import { newAlertsWords, ScreenCard } from './ScreenCard'
import { applyRun } from './stateCache'
import type { RunPush, ScreenerState, Today } from './types'

let client: ReturnType<typeof createQueryClient>
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
)
const settle = () =>
  act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })

function visible(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: state })
}

beforeEach(() => {
  client = createQueryClient()
  api.runCsv.mockReset()
  api.alertsSeen.mockReset()
  saved.save.mockReset()
  visible('visible')
})
afterEach(() => client.clear())

describe('not checked, grouped (GA7.4)', () => {
  const data = {
    count: 200,
    reasons: ['Not checked: warming up', 'Not checked: no price from Upstox yet'],
    groups: [
      { reason: 'Not checked: warming up', count: 148, symbols: ['ACC', 'BEL', 'CIPLA'] },
      { reason: 'Not checked: no price from Upstox yet', count: 2, symbols: ['SBIN', 'TCS'] },
      { reason: 'Not checked: other reasons', count: 50, symbols: [] },
    ],
  }
  it('names a group by its first names and how many more', () => {
    expect(groupNames(data.groups[0])).toBe('ACC, BEL, CIPLA and 145 more')
    expect(groupNames(data.groups[1])).toBe('SBIN, TCS')
    expect(groupNames(data.groups[2])).toBe('50 instruments')
  })
  it('shows each group with its count, its reason in words and its names', async () => {
    render(<NotChecked data={data} />)
    const toggle = screen.getByRole('button', { name: /Not checked \(200\)/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(toggle)
    const rows = screen.getAllByRole('listitem').filter((row) => row.dataset.notChecked)
    expect(rows.map((row) => row.dataset.notChecked)).toEqual(['148', '2', '50'])
    expect(within(rows[0]).getByText('Not checked: warming up')).toBeInTheDocument()
    expect(within(rows[0]).getByText('ACC, BEL, CIPLA and 145 more')).toBeInTheDocument()
    expect(screen.getByText(/Not checked is not a miss/)).toBeInTheDocument()
  })
  it('still reads an answer that has only reasons', async () => {
    render(<NotChecked data={{ count: 3, reasons: ['Not checked: warming up'] }} />)
    await userEvent.click(screen.getByRole('button', { name: /Not checked \(3\)/ }))
    expect(screen.getByText('Not checked: warming up')).toBeInTheDocument()
    expect(screen.queryByText(/not a miss/)).not.toBeInTheDocument()
  })
  it('draws nothing when everything was checked', () => {
    const { container } = render(<NotChecked data={{ count: 0, reasons: [], groups: [] }} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('a run as a file (GA7.5)', () => {
  const FILE = { filename: 'SUDARSHAN_Screener_EMA_2026-11-09_1045.csv', text: 'Rank,Symbol\r\n' }
  it('asks the service for the run and hands the file to the phone', async () => {
    api.runCsv.mockResolvedValue(FILE)
    render(<RunCsv runId={11} />)
    const button = screen.getByRole('button', { name: 'Download CSV' })
    expect(screen.getByText(/prices at the run, not live prices/)).toBeInTheDocument()
    await userEvent.click(button)
    expect(api.runCsv).toHaveBeenCalledExactlyOnceWith(11)
    expect(saved.save).toHaveBeenCalledExactlyOnceWith(FILE)
    expect(screen.getByText(/File: SUDARSHAN_Screener_EMA_2026-11-09_1045\.csv/)).toBeTruthy()
  })
  it('says why in words when the file could not be made, and saves nothing', async () => {
    api.runCsv.mockRejectedValue(
      new ScreenerError({
        code: 'gone',
        message: 'This check is no longer in the history.',
        field: null,
      })
    )
    render(<RunCsv runId={11} />)
    await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This check is no longer in the history.'
    )
    expect(saved.save).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeEnabled()
  })
  it('makes one file however the button is tapped while it is being made', async () => {
    let done: (file: typeof FILE) => void = () => {}
    api.runCsv.mockReturnValue(
      new Promise((resolve) => {
        done = resolve
      })
    )
    render(<RunCsv runId={11} />)
    const button = screen.getByRole('button', { name: 'Download CSV' })
    await userEvent.click(button)
    expect(screen.getByRole('button', { name: 'Making the file' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Making the file' }))
    await act(async () => done(FILE))
    expect(api.runCsv).toHaveBeenCalledTimes(1)
    expect(saved.save).toHaveBeenCalledTimes(1)
  })
})

describe('alerts not looked at (GA7.6)', () => {
  const push = (over: Partial<RunPush> = {}): RunPush => ({
    screen_id: 1,
    run_id: 11,
    run_key: 'k',
    at: 1,
    candle: '',
    counts: { matches: 9, entered: 2, exited: 0, missing: 0, not_checked: 0 },
    entered: [],
    exited: [],
    marks: [],
    ...over,
  })
  const TODAY: Today = { screens: [], recent: [], unseen_alerts: 3 }

  it('a run that raised an alert adds one to its screen, and any other run none', () => {
    const state = screenerState([screenRow({ unseen_alerts: 1 })])
    expect(applyRun(state, push({ alert: true }))?.screens[0].unseen_alerts).toBe(2)
    expect(applyRun(state, push({ alert: false }))?.screens[0].unseen_alerts).toBe(1)
    expect(applyRun(screenerState(), push())?.screens[0].unseen_alerts).toBe(0)
  })

  it('a screen card says how many, with a sign and words', () => {
    expect(newAlertsWords(1)).toBe('1 new alert')
    const { rerender } = render(
      <ScreenCard row={screenRow({ unseen_alerts: 2 })} onOpen={() => {}} />
    )
    const mark = screen.getByText('2 new alerts')
    expect(mark.dataset.unseen).toBe('2')
    expect(mark.querySelector('svg')).not.toBeNull()
    rerender(<ScreenCard row={screenRow({ unseen_alerts: 0 })} onOpen={() => {}} />)
    expect(screen.queryByText(/new alert/)).not.toBeInTheDocument()
  })

  it('opening the screen marks them looked at, up to the run shown, and moves both counts', async () => {
    api.alertsSeen.mockResolvedValue({ seen: 2, unseen_alerts: 1 })
    client.setQueryData<ScreenerState>(
      STATE_KEY,
      screenerState([screenRow({ unseen_alerts: 2 }), screenRow({ id: 2, unseen_alerts: 1 })])
    )
    client.setQueryData<Today>(TODAY_KEY, TODAY)
    renderHook(() => useAlertsSeen(screenRow({ unseen_alerts: 2 })), { wrapper })
    await settle()
    expect(api.alertsSeen).toHaveBeenCalledExactlyOnceWith({ screen_id: 1, through: 10 })
    const held = client.getQueryData<ScreenerState>(STATE_KEY)
    expect(held?.screens.map((row) => row.unseen_alerts)).toEqual([0, 1])
    expect(client.getQueryData<Today>(TODAY_KEY)?.unseen_alerts).toBe(1)
  })

  it('sends nothing for a screen with nothing new', async () => {
    renderHook(() => useAlertsSeen(screenRow({ unseen_alerts: 0 })), { wrapper })
    renderHook(() => useAlertsSeen(screenRow()), { wrapper })
    await settle()
    expect(api.alertsSeen).not.toHaveBeenCalled()
  })

  it('a run that raises one while the screen is open is marked too, once', async () => {
    api.alertsSeen.mockResolvedValue({ seen: 1, unseen_alerts: 0 })
    const view = renderHook(({ row }) => useAlertsSeen(row), {
      wrapper,
      initialProps: { row: screenRow({ unseen_alerts: 0 }) },
    })
    view.rerender({ row: screenRow({ unseen_alerts: 1, last_run_id: 11 }) })
    await settle()
    view.rerender({ row: screenRow({ unseen_alerts: 1, last_run_id: 11 }) })
    await settle()
    expect(api.alertsSeen).toHaveBeenCalledExactlyOnceWith({ screen_id: 1, through: 11 })
  })

  it('waits while the phone shows another app, and marks when the page is back', async () => {
    api.alertsSeen.mockResolvedValue({ seen: 1, unseen_alerts: 0 })
    visible('hidden')
    renderHook(() => useAlertsSeen(screenRow({ unseen_alerts: 1 })), { wrapper })
    await settle()
    expect(api.alertsSeen).not.toHaveBeenCalled()
    visible('visible')
    act(() => void document.dispatchEvent(new Event('visibilitychange')))
    await settle()
    expect(api.alertsSeen).toHaveBeenCalledTimes(1)
  })

  it('is sent once however often the page asks while it is on its way', async () => {
    let done: (answer: { seen: number; unseen_alerts: number }) => void = () => {}
    api.alertsSeen.mockReturnValue(
      new Promise((resolve) => {
        done = resolve
      })
    )
    const first = markAlertsSeen(client, 1, 10)
    const second = markAlertsSeen(client, 1, 10)
    done({ seen: 1, unseen_alerts: 0 })
    await Promise.all([first, second])
    expect(api.alertsSeen).toHaveBeenCalledTimes(1)
    // and a later open asks again
    api.alertsSeen.mockResolvedValue({ seen: 0, unseen_alerts: 0 })
    await markAlertsSeen(client, 1, 10)
    expect(api.alertsSeen).toHaveBeenCalledTimes(2)
  })

  it('a mark that could not be sent leaves the alerts counted', async () => {
    api.alertsSeen.mockRejectedValue(new Error('offline'))
    client.setQueryData<ScreenerState>(STATE_KEY, screenerState([screenRow({ unseen_alerts: 2 })]))
    client.setQueryData<Today>(TODAY_KEY, TODAY)
    await markAlertsSeen(client, 1, 10)
    expect(client.getQueryData<ScreenerState>(STATE_KEY)?.screens[0].unseen_alerts).toBe(2)
    expect(client.getQueryData<Today>(TODAY_KEY)?.unseen_alerts).toBe(3)
  })

  it('a run that came after the mark keeps its screen counted', async () => {
    api.alertsSeen.mockResolvedValue({ seen: 1, unseen_alerts: 1 })
    client.setQueryData<ScreenerState>(
      STATE_KEY,
      screenerState([screenRow({ unseen_alerts: 2, last_run_id: 12 })])
    )
    await markAlertsSeen(client, 1, 10)
    expect(client.getQueryData<ScreenerState>(STATE_KEY)?.screens[0].unseen_alerts).toBe(2)
  })
})
