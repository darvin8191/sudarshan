/**
 * F15 on the page: M8.12, the TradingView import and export sheets. The bridge is the v2 mock; the
 * lists API and the quote read are in-memory stand-ins, as in the other watchlist page tests.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<
    typeof import('@/sudarshan/watchlist/v2/mockBridge').createBridge
  >,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('@/sudarshan/watchlist/v2/mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

const own = vi.hoisted(() => ({
  lists: [] as Array<{
    id: number
    name: string
    position: number
    items: Array<{ id: number; symbol: string; exchange: string; position: number }>
  }>,
}))
vi.mock('@/api/watchlist', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/watchlist')>()),
  watchlistApi: { list: vi.fn(async () => own.lists) },
}))
const calendar = vi.hoisted(() => ({
  value: {
    isLoading: false,
    error: null,
    isMarketOpen: () => true,
    isAnyMarketOpen: () => true,
  },
}))
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar.value }))
vi.mock('@/api/trading', () => ({
  tradingApi: { getMultiQuotes: vi.fn(async () => ({ status: 'success', results: [] })) },
}))
vi.mock('@/sudarshan/watchlist/api', () => ({
  searchSymbols: vi.fn(),
  contractOf: vi.fn(async () => ({ lotSize: 1, tickSize: 0.05 })),
}))
vi.mock('@/api/oi-profile', () => ({
  oiProfileApi: { getUnderlyings: vi.fn(async () => ({ status: 'success', underlyings: [] })) },
}))
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }))
vi.mock('@/utils/toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/utils/toast')>()),
  toast: toasts,
}))

import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import { takeOwn } from '@/sudarshan/watchlist/v2/ownWrites'
import { LIMITS } from '@/sudarshan/watchlist/v2/types'
import Watchlist from './Watchlist'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const TOO_BIG = 'That file is larger than 64 KiB. Export a shorter list and try again.'
const TEXT = '###Banks,NSE:HDFCBANK,NSE:SBIN,###Tech,NSE:INFY,NASDAQ:AAPL'
const preview = (matched: number, hash = 'h1') => ({
  hash,
  sections: 2,
  symbols: 4,
  matched,
  follow: 0,
  left_out: [],
  lists: [{ name: 'Swing', count: matched }],
  words: [
    'Found 2 sections and 4 symbols. 3 match.',
    '1 is not on Indian exchanges and is left out: NASDAQ:AAPL.',
    'Swing · room for 203',
  ],
})
const done = {
  lists: [3],
  added: 3,
  version: '5',
  words: 'Swing: 3 symbols in 2 sections.',
  lists_version: 'a:9',
}

function mount(entry = '/watchlist?sheet=import') {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/watchlist" element={<Watchlist />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const fileOf = (content: string, name = 'Swing.txt') =>
  new File([content], name, { type: 'text/plain' })

const listEntry = (id: number, name: string) => ({
  ref: `list:${id}`,
  kind: 'list',
  name,
  count: 1,
  room: 249,
  locked: false,
  note: null,
})
const stateOf = (enabled: boolean) => ({
  enabled,
  versions: { lists: 'a:1', favourites: 1, flags: 7 },
  flag_names: ['Watch', 'Breakout', 'Results', 'Long term', 'Avoid'].map((name, i) => ({
    mark: i + 1,
    name,
  })),
  favourites: [],
})

async function dialog() {
  return screen.findByRole('dialog', { name: 'Import from TradingView' })
}
async function pasteAndPreview(
  user: ReturnType<typeof userEvent.setup>,
  text = TEXT,
  name = 'Swing'
) {
  const sheet = await dialog()
  await user.type(within(sheet).getByRole('textbox', { name: 'New list name' }), name)
  fireEvent.change(within(sheet).getByRole('textbox', { name: 'Pasted TradingView text' }), {
    target: { value: text },
  })
  await user.click(within(sheet).getByRole('button', { name: 'Preview' }))
  return sheet
}
async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'List options' }))
  await screen.findByRole('menuitem', { name: 'New list' })
}

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  localStorage.clear()
  toasts.success.mockClear()
  useAuthStore.setState({ apiKey: 'key' })
  own.lists = [
    { id: 1, name: 'Main', position: 0, items: [] },
    { id: 2, name: 'Swing', position: 1, items: [] },
  ]
  b().on('state', () => stateOf(true))
  b().on('lists', () => ({
    version: 'a:1',
    own: [listEntry(1, 'Main'), listEntry(2, 'Swing')],
    presets: [
      {
        ref: 'preset:indices',
        kind: 'preset',
        name: 'Indices',
        count: 2,
        room: null,
        locked: true,
        note: null,
      },
    ],
    ranked: [],
    flags: [
      {
        ref: 'flag:2',
        kind: 'flag',
        name: 'Breakout',
        count: 1,
        room: null,
        locked: true,
        note: null,
      },
    ],
    favourites: [],
  }))
  b().on('view', (call) => {
    const ref = (call.params as { ref: string }).ref
    return {
      ref,
      kind: ref.split(':')[0],
      name: 'Main',
      version: '41',
      locked: !ref.startsWith('list:'),
      as_of: null,
      lines: [],
      total: 0,
      missing: [],
      sections: [],
      rows: [],
      follow: [],
      pref: { sort: 'list', descending: false, basis: 'close', held: false },
    }
  })
  b().on('tv_preview', () => preview(3))
  b().on('tv_import', () => done)
})

describe('the size limit is said before reading or sending', () => {
  it('refuses a file over 64 KiB without reading it or sending anything', async () => {
    const read = vi.spyOn(FileReader.prototype, 'readAsText')
    mount()
    const sheet = await dialog()
    const big = fileOf('x'.repeat(LIMITS.tv_file_bytes + 1))
    fireEvent.change(within(sheet).getByLabelText('TradingView file'), { target: { files: [big] } })
    expect(await within(sheet).findByRole('alert')).toHaveTextContent(TOO_BIG)
    expect(read).not.toHaveBeenCalled()
    expect(b().count('tv_preview')).toBe(0)
    read.mockRestore()
  })

  it('refuses pasted text over 64 KiB the same way', async () => {
    const user = userEvent.setup()
    mount()
    const sheet = await pasteAndPreview(user, 'x'.repeat(LIMITS.tv_file_bytes + 1))
    expect(within(sheet).getByRole('alert')).toHaveTextContent(TOO_BIG)
    expect(b().count('tv_preview')).toBe(0)
  })
})

describe('the preview', () => {
  it('asks for a file or pasted text with the file button and the paste box', async () => {
    mount()
    const sheet = await dialog()
    expect(
      within(sheet).getByText(
        'Pick the .txt file you downloaded from TradingView, or paste its text.'
      )
    ).toBeInTheDocument()
    const input = within(sheet).getByLabelText('TradingView file')
    expect(input).toHaveAttribute('accept', '.txt,text/plain')
    expect(within(sheet).getByRole('button', { name: /Choose the .txt file/ })).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Preview' })).toBeDisabled()
  })

  it('the file and the paste path send the same text, and the file name names the new list', async () => {
    const user = userEvent.setup()
    mount()
    const sheet = await dialog()
    fireEvent.change(within(sheet).getByLabelText('TradingView file'), {
      target: { files: [fileOf(TEXT, 'My Swing.txt')] },
    })
    await waitFor(() => expect(b().count('tv_preview')).toBe(1))
    expect(within(sheet).getByRole('textbox', { name: 'New list name' })).toHaveValue('My Swing')
    await user.click(within(sheet).getByRole('button', { name: 'Preview' }))
    await waitFor(() => expect(b().count('tv_preview')).toBe(2))
    const [fromFile, pasted] = b().calls.filter((call) => call.op === 'tv_preview')
    expect(fromFile.body).toEqual({ text: TEXT, name: 'My Swing' })
    expect(pasted.body).toEqual(fromFile.body)
  })

  // Review L11: the preview's lines name the list they were read for.
  it('drops the preview when the new list’s name is changed, until it is read again', async () => {
    const user = userEvent.setup()
    mount()
    const sheet = await pasteAndPreview(user)
    await within(sheet).findAllByRole('listitem')
    await user.type(within(sheet).getByRole('textbox', { name: 'New list name' }), ' 2')
    expect(within(sheet).queryAllByRole('listitem')).toHaveLength(0)
    expect(within(sheet).getByRole('button', { name: /^Import/ })).toBeDisabled()
    await user.click(within(sheet).getByRole('button', { name: 'Preview' }))
    await waitFor(() => expect(b().count('tv_preview')).toBe(2))
    expect(
      b()
        .calls.filter((call) => call.op === 'tv_preview')
        .at(-1)?.body
    ).toEqual({
      text: TEXT,
      name: 'Swing 2',
    })
  })

  it('shows the server’s words in order and writes nothing', async () => {
    const user = userEvent.setup()
    mount()
    const sheet = await pasteAndPreview(user)
    const lines = await within(sheet).findAllByRole('listitem')
    expect(lines.map((line) => line.textContent)).toEqual(preview(3).words)
    expect(b().count('tv_import')).toBe(0)
  })

  it('disables Import when nothing matched, and enables it with the count', async () => {
    const user = userEvent.setup()
    b().on('tv_preview', () => preview(0))
    mount()
    const sheet = await pasteAndPreview(user)
    await within(sheet).findAllByRole('listitem')
    expect(within(sheet).getByRole('button', { name: 'Import 0 symbols' })).toBeDisabled()
    b().on('tv_preview', () => preview(3))
    await user.click(within(sheet).getByRole('button', { name: 'Preview' }))
    expect(await within(sheet).findByRole('button', { name: 'Import 3 symbols' })).toBeEnabled()
  })

  it('drops the preview when the text changes, so Import always sends what was shown', async () => {
    const user = userEvent.setup()
    mount()
    const sheet = await pasteAndPreview(user)
    await within(sheet).findByRole('button', { name: 'Import 3 symbols' })
    fireEvent.change(within(sheet).getByRole('textbox', { name: 'Pasted TradingView text' }), {
      target: { value: `${TEXT},NSE:TCS` },
    })
    expect(within(sheet).getByRole('button', { name: 'Import 0 symbols' })).toBeDisabled()
  })
})

describe('the import', () => {
  it('into a new list sends the text, the hash and the name; says the words; opens the list; skips its push', async () => {
    const user = userEvent.setup()
    b().on('tv_import', () => {
      own.lists = [...own.lists, { id: 3, name: 'Swing 2', position: 2, items: [] }]
      return done
    })
    mount()
    const sheet = await pasteAndPreview(user, TEXT, 'Swing 2')
    await user.click(await within(sheet).findByRole('button', { name: 'Import 3 symbols' }))
    await waitFor(() => expect(b().count('tv_import')).toBe(1))
    expect(b().calls.find((call) => call.op === 'tv_import')?.body).toEqual({
      text: TEXT,
      hash: 'h1',
      name: 'Swing 2',
    })
    await waitFor(() => expect(toasts.success).toHaveBeenCalledWith(done.words))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(await screen.findByRole('tab', { name: /Swing 2/ })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    // The push of this write carries lists_version and is recognised as this page's own.
    expect(takeOwn('a:9')).toBe(true)
  })

  it('into this list sends the list and the version the view answered', async () => {
    const user = userEvent.setup()
    mount()
    const sheet = await dialog()
    await user.click(await within(sheet).findByRole('radio', { name: 'This list (Main)' }))
    fireEvent.change(within(sheet).getByRole('textbox', { name: 'Pasted TradingView text' }), {
      target: { value: TEXT },
    })
    await user.click(within(sheet).getByRole('button', { name: 'Preview' }))
    await waitFor(() => expect(b().count('tv_preview')).toBe(1))
    expect(b().calls.find((call) => call.op === 'tv_preview')?.body).toEqual({
      text: TEXT,
      list: 1,
    })
    await user.click(await within(sheet).findByRole('button', { name: 'Import 3 symbols' }))
    await waitFor(() => expect(b().count('tv_import')).toBe(1))
    expect(b().calls.find((call) => call.op === 'tv_import')?.body).toEqual({
      text: TEXT,
      hash: 'h1',
      list: 1,
      version: '41',
    })
  })

  it('offers "this list" only on an own list', async () => {
    mount('/watchlist?sheet=lists')
    const block = await screen.findByRole('region', { name: 'Flags' })
    await userEvent.setup().click(within(block).getByRole('button', { name: /^Breakout/ }))
    await screen.findByRole('button', { name: 'List options' })
    await userEvent.setup().click(screen.getByRole('button', { name: 'List options' }))
    await userEvent
      .setup()
      .click(await screen.findByRole('menuitem', { name: 'Import from TradingView' }))
    const sheet = await dialog()
    expect(within(sheet).getByRole('radio', { name: 'A new list' })).toBeInTheDocument()
    expect(within(sheet).queryByRole('radio', { name: /This list/ })).toBeNull()
  })

  it.each([
    ['invalid', 'The file changed since its preview. Preview it again.'],
    ['invalid', 'A list named “Swing” is already there. Pick another name.'],
    ['conflict', 'Changed on another screen. The list is shown again.'],
    ['limit', 'You have 50 lists, the most there can be. Delete one to import.'],
    ['limit', 'This file has 312 symbols. A list holds 250, so there is no room in this list.'],
    ['limit', 'A list holds 20 sections, the most there can be. This file has 23.'],
    ['failed', 'Added 2 of 4 symbols to Swing. The rest were not added; try again.'],
  ])('shows the server’s sentence for a refusal (%s): %s', async (code, message) => {
    const user = userEvent.setup()
    b().on('tv_import', () => ({ status: 'error', code, message }))
    mount()
    const sheet = await pasteAndPreview(user)
    await user.click(await within(sheet).findByRole('button', { name: 'Import 3 symbols' }))
    expect(await within(sheet).findByRole('alert')).toHaveTextContent(message)
    expect(toasts.success).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Import from TradingView' })).toBeInTheDocument()
  })
})

const exported = (symbols: number) => ({
  text: symbols ? '###Banks,NSE:HDFCBANK,NSE:SBIN' : '',
  filename: 'Swing.txt',
  symbols,
  sections: 1,
  left_out: [],
  words: symbols
    ? [
        'Swing: 2 symbols in 1 section.',
        '1 section name had a comma, which TradingView cannot read; it was left out.',
      ]
    : ['Nothing in Swing can be exported to TradingView.'],
})

describe('the export', () => {
  it('shows the server’s words and downloads the text as the file name', async () => {
    const user = userEvent.setup()
    b().on('tv_export', () => exported(2))
    const url = vi.fn(() => 'blob:x')
    const revoke = vi.fn()
    Object.assign(URL, { createObjectURL: url, revokeObjectURL: revoke })
    const clicked = vi.fn()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement
    ) {
      clicked(this.download)
    })
    mount('/watchlist')
    await screen.findByRole('tab', { name: /Swing/ })
    await user.click(screen.getByRole('tab', { name: /Swing/ }))
    await openMenu(user)
    await user.click(screen.getByRole('menuitem', { name: 'Export for TradingView' }))
    const sheet = await screen.findByRole('dialog', { name: 'Export for TradingView' })
    const lines = await within(sheet).findAllByRole('listitem')
    expect(lines.map((line) => line.textContent)).toEqual(exported(2).words)
    expect(b().calls.find((call) => call.op === 'tv_export')?.body).toEqual({ ref: 'list:2' })
    await user.click(within(sheet).getByRole('button', { name: /Download Swing.txt/ }))
    expect(clicked).toHaveBeenCalledWith('Swing.txt')
    const blob = (url.mock.calls[0] as unknown as [Blob])[0]
    const read = await new Promise<string>((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.readAsText(blob)
    })
    expect(read).toBe(exported(2).text)
    click.mockRestore()
  })

  it('offers no download when nothing can be exported', async () => {
    b().on('tv_export', () => exported(0))
    mount('/watchlist?sheet=export')
    const sheet = await screen.findByRole('dialog', { name: 'Export for TradingView' })
    expect(
      await within(sheet).findByText('Nothing in Swing can be exported to TradingView.')
    ).toBeInTheDocument()
    expect(within(sheet).queryByRole('button', { name: /Download/ })).toBeNull()
  })

  it('shows the server’s sentence when the export is refused', async () => {
    b().on('tv_export', () => ({
      status: 'error',
      code: 'busy',
      message: 'The instrument list is not loaded yet. Try again in a moment.',
    }))
    mount('/watchlist?sheet=export')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The instrument list is not loaded yet. Try again in a moment.'
    )
  })

  it('is read for the list on screen: a flag list and a built-in list are exported by their own ref', async () => {
    const user = userEvent.setup()
    b().on('tv_export', () => exported(2))
    mount('/watchlist?sheet=lists')
    const block = await screen.findByRole('region', { name: 'Built-in (locked)' })
    await user.click(within(block).getByRole('button', { name: /^Indices/ }))
    await openMenu(user)
    await user.click(screen.getByRole('menuitem', { name: 'Export for TradingView' }))
    await screen.findByRole('dialog', { name: 'Export for TradingView' })
    await waitFor(() =>
      expect(b().calls.find((call) => call.op === 'tv_export')?.body).toEqual({
        ref: 'preset:indices',
      })
    )
  })
})

describe('the list menu', () => {
  it('offers both items on an own list and Export alone for a flag list', async () => {
    const user = userEvent.setup()
    mount('/watchlist')
    await openMenu(user)
    expect(screen.getByRole('menuitem', { name: 'Import from TradingView' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Export for TradingView' })).toBeInTheDocument()
  })

  it('has neither item, and no sheet by address, with the switch off', async () => {
    const user = userEvent.setup()
    b().on('state', () => stateOf(false))
    mount('/watchlist?sheet=import')
    await screen.findByRole('tab', { name: /Main/ })
    await openMenu(user)
    expect(screen.queryByRole('menuitem', { name: /TradingView/ })).toBeNull()
    expect(screen.queryByRole('dialog', { name: /TradingView/ })).toBeNull()
    expect(b().count('tv_preview') + b().count('tv_export')).toBe(0)
  })
})

describe('the 500 ms guard', () => {
  it('ignores taps on the import sheet for its first half second', async () => {
    OPEN_GUARD.ms = 500
    const pick = vi.spyOn(HTMLInputElement.prototype, 'click')
    mount()
    const sheet = await dialog()
    fireEvent.click(within(sheet).getByRole('button', { name: /Choose the .txt file/ }))
    expect(pick).not.toHaveBeenCalled()
    await waitFor(() => expect(sheet).not.toHaveAttribute('data-settling'), { timeout: 2000 })
    fireEvent.click(within(sheet).getByRole('button', { name: /Choose the .txt file/ }))
    expect(pick).toHaveBeenCalledTimes(1)
    pick.mockRestore()
  })
})
