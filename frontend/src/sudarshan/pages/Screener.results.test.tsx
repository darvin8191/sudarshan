/**
 * The Results tab (M9.16) in jsdom: the rows of the latest run with their live figures, the chip
 * on every row as an icon and a word, a pushed run patching the list at once, a row past the live
 * cap keeping the price of the run and saying so, a stopped feed greying a row with how old it is,
 * and the screen never claiming to be current while it is not in the owner's room.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ results: vi.fn() }))
vi.mock('@/sudarshan/screener/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/screener/api')>()),
  screenerApi: api,
}))

const live = vi.hoisted(() => ({
  rows: [] as unknown[],
  followed: [] as string[],
}))
vi.mock('@/sudarshan/watchlist/useLiveRows', () => ({
  useLiveRows: ({ items }: { items: Array<{ symbol: string; exchange: string }> }) => {
    live.followed = items.map((item) => `${item.exchange}:${item.symbol}`)
    return {
      rows: live.rows,
      line: null,
      now: Date.UTC(2026, 10, 9, 5, 15, 30),
      feed: { connected: true },
      snapshot: null,
      retry: () => {},
    }
  },
}))

import { createQueryClient } from '@/app/providers'
import { resultsKey } from '@/sudarshan/screener/keys'
import { onRun } from '@/sudarshan/screener/push'
import { NOT_RECEIVING, Results } from '@/sudarshan/screener/Results'
import type { Chip, Results as ResultsData } from '@/sudarshan/screener/types'
import { NOW, resultRow, runRow, screenRow } from '@/test/screener'

const CHIP: Chip = {
  kind: 'new',
  text: 'New',
  icon: 'circle-plus',
  tone: 'brand',
  name: 'New in this screen today',
  note: '',
}

function data(
  rows = [resultRow('INFY', { chip: CHIP }), resultRow('SBIN', { rank: 2 })]
): ResultsData {
  return {
    screen_id: 1,
    run: runRow(10, { at: NOW }),
    rows,
    not_checked: { count: 0, reasons: [] },
  }
}

function liveRow(symbol: string, over: Record<string, unknown> = {}) {
  return {
    item: { id: 1, symbol, exchange: 'NSE', position: 0 },
    key: `NSE:${symbol}`,
    view: {
      ltp: 1500.5,
      prevClose: 1480,
      at: Date.UTC(2026, 10, 9, 5, 15, 10),
      live: true,
      change: 20.5,
      changePercent: 1.38,
    },
    direction: 'up',
    stale: false,
    tickAt: null,
    ...over,
  }
}

let client: ReturnType<typeof createQueryClient>

function show(hearing: 'joined' | 'not_joined' | 'unknown' = 'joined', onOpen = vi.fn()) {
  return render(
    <QueryClientProvider client={client}>
      <Results
        screen={screenRow()}
        apiKey="k"
        isOpen={() => true}
        hearing={hearing}
        openKey={null}
        onOpen={onOpen}
      />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  client = createQueryClient()
  api.results.mockReset()
  live.rows = []
})
afterEach(() => client.clear())

describe('Results', () => {
  it('lists the run, the values and a chip with an icon and a word on the row that has one', async () => {
    api.results.mockResolvedValue(data())
    live.rows = [liveRow('INFY'), liveRow('SBIN')]
    show()
    const infy = await screen.findByText('INFY')
    const row = infy.closest('li') as HTMLElement
    expect(within(row).getByRole('img', { name: CHIP.name })).toBeInTheDocument()
    expect(within(row).getByText('New')).toBeInTheDocument()
    expect(within(row).getByText('RSI 61.2')).toBeInTheDocument()
    expect(within(row).getByText(/1,500\.50/)).toBeInTheDocument()
    expect(screen.getByText(/Run at .* · 10:00 to 10:15 candle/)).toBeInTheDocument()
  })

  it('marks a value read from a forming candle with a word', async () => {
    api.results.mockResolvedValue(data([resultRow('INFY', { forming: true })]))
    show()
    expect(await screen.findByText('forming')).toBeInTheDocument()
  })

  it('greys a row whose ticks stopped and says how old its price is', async () => {
    api.results.mockResolvedValue(data())
    live.rows = [liveRow('INFY', { stale: true }), liveRow('SBIN')]
    show()
    const row = (await screen.findByText('INFY')).closest('li') as HTMLElement
    expect(row.dataset.stale).toBe('true')
    expect(within(row).getByText(/Stale · updated 20s/)).toBeInTheDocument()
  })

  it('follows at most 100 and gives the rest the price at the run, labelled', async () => {
    const many = Array.from({ length: 103 }, (_, i) =>
      resultRow(`S${String(i).padStart(3, '0')}`, { rank: i + 1 })
    )
    api.results.mockResolvedValue(data(many))
    live.rows = []
    show()
    await screen.findByText('S000')
    expect(live.followed).toHaveLength(100)
    expect(live.followed).not.toContain('NSE:S102')
    const past = screen.getByText('S102').closest('li') as HTMLElement
    expect(within(past).getByText(/at the run/)).toBeInTheDocument()
  })

  it('patches the list when a run is pushed, without waiting for a read', async () => {
    api.results.mockResolvedValue(data())
    show()
    await screen.findByText('INFY')
    // The read that follows a push never answers here: the patch alone must be on screen.
    api.results.mockReturnValue(new Promise(() => {}))
    act(() => {
      client.setQueryData(resultsKey(1), data())
      onRun(client, {
        screen_id: 1,
        run_id: 11,
        run_key: 'k11',
        at: NOW + 900_000,
        candle: '10:15 to 10:30',
        counts: { matches: 2, entered: 1, exited: 1, missing: 0, not_checked: 0 },
        entered: [resultRow('TCS', { rank: 1, chip: CHIP })],
        exited: [{ symbol: 'SBIN', exchange: 'NSE' }],
        marks: [],
      })
    })
    expect(await screen.findByText('TCS')).toBeInTheDocument()
    expect(screen.queryByText('SBIN')).not.toBeInTheDocument()
    expect(screen.getByText(/10:15 to 10:30 candle/)).toBeInTheDocument()
  })

  it('says it is not being kept current while this screen is not in the owner room', async () => {
    api.results.mockResolvedValue(data())
    show('not_joined')
    expect(await screen.findByText(NOT_RECEIVING)).toBeInTheDocument()
  })

  it('does not say so while the room is joined', async () => {
    api.results.mockResolvedValue(data())
    show('joined')
    await screen.findByText('INFY')
    expect(screen.queryByText(NOT_RECEIVING)).not.toBeInTheDocument()
  })

  it('opens a row on a tap', async () => {
    api.results.mockResolvedValue(data())
    const onOpen = vi.fn()
    show('joined', onOpen)
    await userEvent.click((await screen.findByText('INFY')).closest('button') as HTMLElement)
    expect(onOpen).toHaveBeenCalledWith('NSE:INFY')
  })

  it('lists why some instruments were not checked, in the service words', async () => {
    api.results.mockResolvedValue({
      ...data(),
      not_checked: { count: 3, reasons: ['2 had too few candles', '1 has no open interest'] },
    })
    show()
    const toggle = await screen.findByRole('button', { name: /Not checked \(3\)/ })
    expect(screen.queryByText('2 had too few candles')).not.toBeInTheDocument()
    await userEvent.click(toggle)
    expect(screen.getByText('2 had too few candles')).toBeInTheDocument()
    expect(screen.getByText('1 has no open interest')).toBeInTheDocument()
  })

  it('shows an empty state when nothing matches', async () => {
    api.results.mockResolvedValue(data([]))
    show()
    expect(await screen.findByText('Nothing matches at the last check')).toBeInTheDocument()
  })

  it('says it could not be read, with a way to try again', async () => {
    api.results.mockRejectedValue(new Error('x'))
    show()
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})
