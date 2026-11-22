import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/components/socket/SocketProvider', () => ({
  SocketProvider: ({ children }: { children: ReactNode }) => children,
  useSocketContext: () => ({ socket: null, playAlertSound: () => {} }),
}))
const toasts = vi.hoisted(() => [] as string[])
vi.mock('@/utils/toast', () => {
  const say = (text: string) => {
    toasts.push(text)
  }
  return { showToast: { success: say, error: say, warning: say, info: say } }
})

import Historify from '@/pages/Historify'
import { useAuthStore } from '@/stores/authStore'
import { useBrokerStore } from '@/stores/brokerStore'
import { DOWNLOAD_LIST, downloadListWords } from './historifyWords'

describe('downloadListWords (M8.19)', () => {
  it.each([
    ['Added RELIANCE to watchlist', 'Added RELIANCE to download list'],
    ['RELIANCE already in watchlist', 'RELIANCE already in download list'],
    [
      'Removed 3 symbol(s) from watchlist, 1 not found',
      'Removed 3 symbol(s) from download list, 1 not found',
    ],
    ['Watchlist is empty', 'Download list is empty'],
    ['Watchlist cleared', 'Download list cleared'],
    ['watchlist', 'download list'],
  ])('%s', (said, shown) => {
    expect(downloadListWords(said)).toBe(shown)
  })

  it('changes whole words only and leaves every other sentence as it is', () => {
    expect(downloadListWords('Your watchlists are elsewhere')).toBe('Your watchlists are elsewhere')
    expect(downloadListWords('Job started: 4 symbols')).toBe('Job started: 4 symbols')
    expect(downloadListWords('')).toBe('')
  })
})

/** Every word a person can read or hear on the page: its text, labels, titles and placeholders. */
function readable(root: HTMLElement): string {
  const parts = [root.textContent ?? '']
  for (const element of root.querySelectorAll('*'))
    for (const name of ['aria-label', 'title', 'placeholder'])
      parts.push(element.getAttribute(name) ?? '')
  return parts.join(' ')
}

const DATA: Record<string, unknown> = {
  '/auth/csrf-token': { csrf_token: 'test' },
  '/historify/api/watchlist': {
    status: 'success',
    data: [{ id: 1, symbol: 'INFY', exchange: 'NSE', added_at: '2026-11-20T10:00:00' }],
  },
  '/historify/api/catalog': { status: 'success', data: [] },
  '/historify/api/intervals': { status: 'success', data: { intervals: [] } },
  '/historify/api/historify-intervals': {
    status: 'success',
    data: { storage_intervals: ['1m', 'D'], computed_intervals: [], all_intervals: ['1m', 'D'] },
  },
  '/historify/api/stats': {
    status: 'success',
    data: { database_size_mb: 1, total_records: 10, total_symbols: 1, watchlist_count: 1 },
  },
  '/historify/api/exchanges': { status: 'success', data: ['NSE', 'BSE'] },
  '/historify/api/jobs?limit=50': {
    status: 'success',
    data: [
      {
        id: 'j1',
        job_type: 'watchlist',
        status: 'completed',
        total_symbols: 1,
        completed_symbols: 1,
        failed_symbols: 0,
        interval: 'D',
        start_date: '2026-01-01',
        end_date: '2026-11-20',
        created_at: '2026-11-20T10:00:00',
      },
    ],
  },
  '/historify/api/schedules': { status: 'success', data: [] },
}

beforeEach(() => {
  toasts.length = 0
  useAuthStore.setState({
    isAuthenticated: true,
    user: { username: 'owner', broker: 'upstox' } as never,
  })
  useBrokerStore.setState({ isLoaded: true, capabilities: null })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/historify/api/watchlist' && init?.method === 'POST')
        return new Response(
          JSON.stringify({ status: 'success', message: 'Added TCS to watchlist' })
        )
      const body = DATA[url] ?? { status: 'success', data: [] }
      return new Response(JSON.stringify(body))
    })
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.setState({ user: null, isAuthenticated: false })
})

function mount() {
  return render(
    <MemoryRouter initialEntries={['/historify']}>
      <Historify />
    </MemoryRouter>
  )
}

describe("Historify's list is the Download list (M8.19)", () => {
  it('says watchlist nowhere a person reads or hears it, on any tab', async () => {
    mount()
    await screen.findByText('INFY')
    const tab = screen.getByRole('tab', { name: DOWNLOAD_LIST })
    expect(tab).toHaveAttribute('aria-selected', 'true')
    expect(readable(document.body)).not.toMatch(/watch/i)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /Jobs/ }))
    fireEvent.click(screen.getByRole('tab', { name: /Jobs/ }))
    await waitFor(() => expect(screen.getByText('download list')).toBeInTheDocument())
    expect(readable(document.body)).not.toMatch(/watchlist/i)

    fireEvent.mouseDown(screen.getByRole('tab', { name: /Sched/ }))
    fireEvent.click(screen.getByRole('tab', { name: /Sched/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Create Schedule/ }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByPlaceholderText('Daily download list update')).toBeInTheDocument()
    expect(readable(document.body)).not.toMatch(/watchlist/i)
  })

  it("shows the server's sentence after an add in the page's words", async () => {
    mount()
    await screen.findByText('INFY')
    fireEvent.change(screen.getByPlaceholderText('Search symbol to add...'), {
      target: { value: 'TCS' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(toasts).toContain('Added TCS to download list'))
  })
})
