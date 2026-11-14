/**
 * The System page (M20.8): what the server's watch last saw, each line with an icon and a word,
 * an old or missing report said as such, and Disconnect the assistant: asked first, sent once.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ status: vi.fn(), disconnect: vi.fn() }))
vi.mock('@/api/system', () => ({ systemApi: api }))

import System from './System'

const NOW = Math.floor(Date.now() / 1000)
const ASSISTANT = {
  switched_on: false,
  read_only: true,
  write_scope_offered: false,
  approval_required: true,
  running: false,
  registered: false,
  address: '',
  words: 'The assistant is switched off.',
}
const CHECKS = [
  { name: 'service_feed', level: 'fine', told: 'fine', since: null, facts: {} },
  { name: 'service_family', level: 'off', told: 'fine', since: null, facts: {} },
  {
    name: 'service_screener',
    level: 'failing',
    told: 'failing',
    since: NOW - 300,
    facts: { service: 'screener', minutes: '4' },
  },
  { name: 'login', level: 'off', told: 'fine', since: null, facts: {} },
  {
    name: 'disk',
    level: 'warning',
    told: 'warning',
    since: NOW - 7200,
    facts: { free_gb: '12.0', cause: 'space' },
  },
  { name: 'certificate', level: 'unread', told: 'fine', since: null, facts: {} },
  {
    name: 'backup',
    level: 'fine',
    told: 'fine',
    since: null,
    facts: { age_h: '17', size_mb: '412', offsite: 'ok' },
  },
  { name: 'drill', level: 'warning', told: 'warning', since: null, facts: { cause: 'never' } },
  { name: 'posture_locks', level: 'fine', told: 'fine', since: null, facts: {} },
  {
    name: 'assistant',
    level: 'off',
    told: 'fine',
    since: null,
    facts: { clients: '0', families: '0' },
  },
  { name: 'telegram_links', level: 'fine', told: 'fine', since: null, facts: { links: '1' } },
  { name: 'a_check_from_a_newer_server', level: 'fine', told: 'fine', since: null, facts: {} },
]

function status(over: object = {}) {
  return {
    watch: {
      at: NOW - 40,
      release: 'v0.13.1',
      heartbeat: {
        state: 'not_set',
        words: 'No outside heartbeat is set up: nobody is told if the whole server stops.',
      },
      checks: CHECKS,
      age_s: 40,
      stale: false,
    },
    drill: null,
    assistant: ASSISTANT,
    sessions: 2,
    ...over,
  }
}

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/system']}>
        <System />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  api.status.mockReset()
  api.disconnect.mockReset()
})

describe('the System page', () => {
  it('shows each check with its word, its line and since when', async () => {
    api.status.mockResolvedValue(status())
    show()
    const services = await screen.findByRole('region', { name: 'Services' })
    const rows = within(services).getAllByRole('listitem')
    expect(rows.map((row) => row.querySelector('[data-level]')?.textContent)).toEqual([
      'Fine',
      'Off',
      'Not working',
    ])
    expect(within(rows[2]).getByText('Screener')).toBeInTheDocument()
    expect(rows[2]).toHaveTextContent('Not answering for 4 minutes.')
    expect(rows[2]).toHaveTextContent(/Since \d\d:\d\d/)
    expect(rows[0]).not.toHaveTextContent('Since')
    for (const mark of services.querySelectorAll('[data-level]')) {
      expect(mark.querySelector('svg')).not.toBeNull() // an icon beside the word, never colour alone
    }
    expect(screen.getByTestId('system-summary')).toHaveTextContent(
      /^3 to look at\. Release v0\.13\.1, looked at \d\d:\d\d\.$/
    )
    const server = screen.getByRole('region', { name: 'Server' })
    expect(server).toHaveTextContent('12.0 GB free. Getting full.')
    expect(server).toHaveTextContent('Could not be read')
    expect(server).toHaveTextContent('Outside heartbeat. No outside heartbeat is set up')
    expect(screen.getByRole('region', { name: 'Backups' })).toHaveTextContent('Never done yet')
    const connections = screen.getByRole('region', { name: 'Connections' })
    expect(connections).toHaveTextContent('Your signed-in devices: 2.')
    expect(within(connections).getByRole('link', { name: 'Profile' })).toHaveAttribute(
      'href',
      '/profile'
    )
    expect(screen.queryByText(/a_check_from_a_newer_server/)).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('says so when the watch has not reported, or not lately', async () => {
    api.status.mockResolvedValue(status({ watch: null }))
    const first = show()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "The server's watch has not reported yet."
    )
    expect(screen.queryByTestId('system-summary')).toBeNull()
    expect(screen.getByRole('region', { name: 'AI assistant' })).toBeInTheDocument()
    first.unmount()
    const old = status()
    old.watch.stale = true
    old.watch.age_s = 3600
    api.status.mockResolvedValue(old)
    show()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /^The watch has not looked since .*What is below is old: have the server looked at\.$/
    )
  })

  it('says the state could not be read, and tries again', async () => {
    api.status.mockRejectedValueOnce(new Error('no'))
    api.status.mockResolvedValue(status())
    show()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "The server's state could not be read."
    )
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByTestId('system-summary')).toBeInTheDocument()
  })

  it('reads again on request', async () => {
    api.status.mockResolvedValue(status())
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Read again' }))
    await waitFor(() => expect(api.status).toHaveBeenCalledTimes(2))
  })

  it('asks before it disconnects the assistant, sends once, and says the answer', async () => {
    api.status.mockResolvedValue(
      status({
        assistant: {
          ...ASSISTANT,
          switched_on: true,
          running: true,
          words:
            'The assistant is switched on, read-only. It cannot place, change or cancel orders.',
        },
      })
    )
    let finish: (answer: { ok: boolean; message: string }) => void = () => {}
    api.disconnect.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    show()
    const card = await screen.findByRole('region', { name: 'AI assistant' })
    expect(card).toHaveTextContent('It cannot place, change or cancel orders.')
    await userEvent.click(within(card).getByRole('button', { name: 'Disconnect the assistant' }))
    expect(api.disconnect).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Every AI connection to SUDARSHAN ends now.')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Keep connected' }))
    expect(api.disconnect).not.toHaveBeenCalled()
    await userEvent.click(within(card).getByRole('button', { name: 'Disconnect the assistant' }))
    const again = await screen.findByRole('dialog')
    const confirm = within(again).getByRole('button', { name: 'Disconnect' })
    await userEvent.click(confirm)
    await userEvent.click(confirm)
    expect(api.disconnect).toHaveBeenCalledTimes(1)
    finish({ ok: true, message: 'Every AI connection is ended.' })
    expect(await within(card).findByRole('status')).toHaveTextContent(
      'Every AI connection is ended.'
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows a disconnect that did not work as an alert with what to do', async () => {
    api.status.mockResolvedValue(status())
    api.disconnect.mockResolvedValue({
      ok: false,
      message: 'Turn on the kill switch on the Risk page now.',
    })
    show()
    const card = await screen.findByRole('region', { name: 'AI assistant' })
    await userEvent.click(within(card).getByRole('button', { name: 'Disconnect the assistant' }))
    await userEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Disconnect' })
    )
    expect(await within(card).findByRole('alert')).toHaveTextContent(
      'Turn on the kill switch on the Risk page now.'
    )
  })

  it('warns when a new AI client would be approved without asking', async () => {
    api.status.mockResolvedValue(
      status({
        assistant: { ...ASSISTANT, switched_on: true, running: true, approval_required: false },
      })
    )
    show()
    const card = await screen.findByRole('region', { name: 'AI assistant' })
    expect(card).toHaveTextContent('MCP_OAUTH_REQUIRE_APPROVAL=True')
  })
})
