/**
 * History and rewind (M9.17) in jsdom: runs newest first with the candle, the count, who entered
 * and who left, and the service's marks as an icon and words; older runs a page at a time by the
 * oldest run held; a pushed run joining the top once; a past run opened as a set that says its
 * prices are from the run and not live.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ runs: vi.fn(), run: vi.fn() }))
vi.mock('@/sudarshan/screener/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/screener/api')>()),
  screenerApi: api,
}))

import { createQueryClient } from '@/app/providers'
import { History } from '@/sudarshan/screener/History'
import { onRun } from '@/sudarshan/screener/push'
import { RunSheet } from '@/sudarshan/screener/RunSheet'
import { NOW, resultRow, runRow } from '@/test/screener'

let client: ReturnType<typeof createQueryClient>

function showHistory(onOpen = vi.fn(), hearing: 'joined' | 'not_joined' = 'joined') {
  render(
    <QueryClientProvider client={client}>
      <History screenId={1} hearing={hearing} onOpen={onOpen} />
    </QueryClientProvider>
  )
  return onOpen
}

beforeEach(() => {
  client = createQueryClient()
  api.runs.mockReset()
  api.run.mockReset()
})
afterEach(() => client.clear())

describe('History', () => {
  it('lists runs with their counts, candle and marks as icon and words', async () => {
    api.runs.mockResolvedValue({
      runs: [
        runRow(12, { matches: 1, entered: 1, exited: 0, marks: ['2 instruments had no candle'] }),
        runRow(11, { at: NOW - 900_000, matches: 7, entered: 2, exited: 1 }),
      ],
      more: false,
    })
    showHistory()
    const first = (await screen.findByText('1 match')).closest('li') as HTMLElement
    expect(within(first).getByText('1 entered')).toBeInTheDocument()
    expect(within(first).getByText('0 exited')).toBeInTheDocument()
    expect(within(first).getByText('2 instruments had no candle')).toBeInTheDocument()
    expect(within(first).getByText(/10:00 to 10:15 candle/)).toBeInTheDocument()
    const rows = screen.getAllByRole('listitem')
    expect(rows[0].dataset.run).toBe('12')
    expect(rows[1].dataset.run).toBe('11')
  })

  it('asks for older runs before the oldest one held, and offers it only while there are more', async () => {
    api.runs.mockResolvedValueOnce({ runs: [runRow(12), runRow(11)], more: true })
    showHistory()
    await screen.findAllByText('7 matches')
    api.runs.mockResolvedValueOnce({ runs: [runRow(10)], more: false })
    await userEvent.click(screen.getByRole('button', { name: 'Show older runs' }))
    await vi.waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(3))
    expect(api.runs).toHaveBeenLastCalledWith(1, { before: 11, limit: 30 }, expect.anything())
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
    expect(screen.queryByRole('button', { name: 'Show older runs' })).not.toBeInTheDocument()
  })

  it('puts a pushed run at the top once', async () => {
    api.runs.mockResolvedValue({ runs: [runRow(12)], more: false })
    showHistory()
    await screen.findAllByText('7 matches')
    api.runs.mockReturnValue(new Promise(() => {}))
    const push = {
      screen_id: 1,
      run_id: 13,
      run_key: 'k13',
      at: NOW + 900_000,
      candle: '10:15 to 10:30',
      counts: { matches: 4, entered: 1, exited: 0, missing: 0, not_checked: 0 },
      entered: [],
      exited: [],
      marks: [],
    }
    act(() => {
      onRun(client, push)
      onRun(client, push)
    })
    await vi.waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(2))
    expect(screen.getAllByRole('listitem').map((row) => row.dataset.run)).toEqual(['13', '12'])
  })

  it('says new runs may be missing while the screen is not receiving updates', async () => {
    api.runs.mockResolvedValue({ runs: [runRow(12)], more: false })
    showHistory(vi.fn(), 'not_joined')
    expect(await screen.findByText(/New runs may be missing/)).toBeInTheDocument()
  })

  it('opens a run on a tap', async () => {
    api.runs.mockResolvedValue({ runs: [runRow(12)], more: false })
    const onOpen = showHistory()
    await userEvent.click((await screen.findByText('7 matches')).closest('button') as HTMLElement)
    expect(onOpen).toHaveBeenCalledWith(12)
  })

  it('shows an empty state before the first run', async () => {
    api.runs.mockResolvedValue({ runs: [], more: false })
    showHistory()
    expect(await screen.findByText('No runs yet')).toBeInTheDocument()
  })
})

describe('RunSheet', () => {
  it('rewinds to the run and says its prices are not live', async () => {
    api.run.mockResolvedValue({
      screen_id: 1,
      run: runRow(11, { matches: 2 }),
      rows: [resultRow('INFY', { ltp: 1500.5 }), resultRow('SBIN', { rank: 2 })],
      not_checked: { count: 1, reasons: ['1 had too few candles'] },
    })
    render(
      <QueryClientProvider client={client}>
        <RunSheet runId={11} onClose={() => {}} />
      </QueryClientProvider>
    )
    expect(await screen.findByText(/Prices are from the run, not live/)).toBeInTheDocument()
    expect(api.run).toHaveBeenCalledWith(11, expect.anything())
    const row = screen.getByText('INFY').closest('li') as HTMLElement
    expect(within(row).getByText(/at the run/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Not checked \(1\)/ })).toBeInTheDocument()
  })

  it('stays shut with no run', () => {
    render(
      <QueryClientProvider client={client}>
        <RunSheet runId={null} onClose={() => {}} />
      </QueryClientProvider>
    )
    expect(api.run).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
