/**
 * F8, flags and notes on the phone: the markers on a row, the symbol sheet's Flag, Note and In
 * lists, the Flags block of All lists (rename, Clear expired), the family render and the switch
 * off. The bridge is the v2 mock; the live rows are a stand-in (`useLiveRows` mocked).
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./v2/mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./v2/mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

const feed = vi.hoisted(() => ({ state: null as unknown }))
vi.mock('./useLiveRows', () => ({ useLiveRows: () => feed.state }))
const toast = vi.hoisted(() => ({
  info: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ showToast: toast }))

import { useState } from 'react'
import { createQueryClient } from '@/app/providers'
import { LiveList } from './LiveList'
import { clearNoteDraftsForTests } from './noteDrafts'
import { OPEN_GUARD } from './openGuard'
import { rowView } from './rows'
import { NOTE_KEPT, NOTE_NOT_SAVED, NOTE_SAVED } from './SymbolMarks'
import type { LiveRow } from './useLiveRows'
import { NOTE_LONG, PRIVATE_WORDS } from './v2/marks'
import type { FlagName, Row, SymbolInfo, View } from './v2/types'
import { useListView } from './v2/useListView'

const b = () => bridge.current as NonNullable<typeof bridge.current>
const NAMES: FlagName[] = [
  { mark: 1, name: 'Watch' },
  { mark: 2, name: 'Breakout' },
  { mark: 3, name: 'Results' },
  { mark: 4, name: 'Long term' },
  { mark: 5, name: 'Avoid' },
]
const CONFLICT = 'Changed on another screen. The list is shown again.'

const SYMBOLS = ['RELIANCE', 'TCS', 'INFY']
const liveRows = (): LiveRow[] =>
  SYMBOLS.map((symbol, index) => ({
    item: { id: index + 1, symbol, exchange: 'NSE', position: index },
    key: `NSE:${symbol}`,
    view: rowView(undefined, undefined, 'close'),
    direction: null,
    stale: false,
    tickAt: null,
  }))
const rowOf = (symbol: string, flag: number | null, note: boolean, expired = false): Row => ({
  item: null,
  contract: {
    exchange: 'NSE',
    symbol,
    key: `NSE:${symbol}`,
    name: symbol,
    lot: null,
    tick: null,
    expiry: null,
    isin: null,
  },
  section_id: null,
  flag,
  has_note: note,
  expired,
  metric: null,
})
const viewOf = (rows: Row[]): View =>
  ({
    ref: 'list:1',
    kind: 'list',
    version: 'a:1',
    pref: { sort: 'list', descending: false, basis: 'close', held: false },
    sections: [],
    rows,
    follow: [],
  }) as unknown as View

const info = (over: Partial<SymbolInfo> = {}): SymbolInfo => ({
  key: 'NSE:RELIANCE',
  flag: 2,
  flag_name: 'Breakout',
  note: 'Wait for the retest',
  note_at: 1_760_000_000_000,
  expired: false,
  version: 3,
  lists: [
    { ref: 'list:1', name: 'Swing' },
    { ref: 'list:2', name: 'Momentum' },
    { ref: 'preset:nifty50', name: 'NIFTY 50' },
  ],
  ...over,
})

const server = { info: info(), rows: [] as Row[] }

function Page({
  v2 = true,
  readOnly = false,
  openItemId = null,
}: {
  v2?: boolean
  readOnly?: boolean
  openItemId?: number | null
}) {
  const data = useListView('list:1').data
  return (
    <LiveList
      items={liveRows().map((row) => row.item)}
      apiKey={null}
      isOpen={() => true}
      loading={false}
      editing={false}
      listName="Mine"
      openItemId={openItemId}
      panel={{ 'aria-label': 'Watchlist' }}
      onOpen={() => {}}
      onMove={() => {}}
      onRemove={() => {}}
      onCloseSheet={() => {}}
      onOrder={() => {}}
      onAlert={() => {}}
      view={v2 ? data : undefined}
      flagNames={v2 ? NAMES : undefined}
      readOnly={readOnly}
    />
  )
}

/** The page with its sheet open on RELIANCE, closed by the sheet's own Close (GA10.8). */
function ClosablePage() {
  const data = useListView('list:1').data
  const [open, setOpen] = useState<number | null>(1)
  return (
    <LiveList
      items={liveRows().map((row) => row.item)}
      apiKey={null}
      isOpen={() => true}
      loading={false}
      editing={false}
      listName="Mine"
      openItemId={open}
      panel={{ 'aria-label': 'Watchlist' }}
      onOpen={(id) => setOpen(id)}
      onMove={() => {}}
      onRemove={() => {}}
      onCloseSheet={() => setOpen(null)}
      onOrder={() => {}}
      onAlert={() => {}}
      view={data}
      flagNames={NAMES}
    />
  )
}

async function mount(props: Parameters<typeof Page>[0] = {}) {
  const out = render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <Page {...props} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  await waitFor(() => expect(b().count('view')).toBeGreaterThan(0))
  await act(async () => {})
  return out
}

beforeEach(() => {
  b().reset()
  vi.clearAllMocks()
  clearNoteDraftsForTests()
  OPEN_GUARD.ms = 0
  server.info = info()
  server.rows = [rowOf('RELIANCE', 2, true), rowOf('TCS', null, false), rowOf('INFY', 5, false)]
  feed.state = {
    rows: liveRows(),
    line: null,
    now: 1_760_000_000_000,
    feed: { connected: true },
    snapshot: 'ready',
    read: true,
    retry: () => {},
  }
  b().on('state', () => ({ enabled: true }))
  b().on('view', () => viewOf(server.rows))
  b().on('symbol', () => server.info)
  b().on('mark', (call) => {
    const body = call.body as { flag?: number | null; note?: string | null }
    server.info = {
      ...server.info,
      ...('flag' in body ? { flag: body.flag ?? null } : {}),
      ...('note' in body ? { note: body.note ?? null } : {}),
      version: (server.info.version ?? 0) + 1,
    }
    return { version: String(server.info.version), id: null, words: null }
  })
})

const row = (name: string) => screen.getByText(name).closest('li') as HTMLElement

describe('the markers on a row', () => {
  it('shows the flag glyph before the name, a note icon after it, and says both in words', async () => {
    await mount()
    const reliance = await waitFor(() => {
      const el = row('RELIANCE')
      expect(el.querySelector('[data-mark="2"]')).not.toBeNull()
      return el
    })
    const glyph = reliance.querySelector('[data-mark="2"]') as Element
    const title = within(reliance).getByText('RELIANCE')
    // Glyph, then the name, then the note icon: one shape and one word, never a colour alone.
    expect(glyph.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const note = reliance.querySelector('[data-note="true"]') as Element
    expect(title.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(glyph).toHaveAttribute('aria-hidden', 'true')
    expect(within(reliance).getByText('Flag: Breakout. Has a note.')).toBeInTheDocument()
    // A flag with no note says only the flag; a bare row says nothing.
    expect(within(row('INFY')).getByText('Flag: Avoid.')).toBeInTheDocument()
    expect(row('TCS').querySelector('[data-mark],[data-note]')).toBeNull()
  })

  it('shows an expired contract with a clock and the word', async () => {
    server.rows = [
      rowOf('RELIANCE', 1, false, true),
      rowOf('TCS', null, false),
      rowOf('INFY', null, false),
    ]
    await mount()
    const el = await waitFor(() => {
      const found = row('RELIANCE').querySelector('[data-expired="true"]')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    expect(el).toHaveTextContent('Expired')
    expect(el.querySelector('svg')).not.toBeNull()
  })

  it('draws none of it, and reads nothing private, with the switch off', async () => {
    await mount({ v2: false, openItemId: 1 })
    expect(document.querySelector('[data-mark],[data-note]')).toBeNull()
    expect(screen.queryByRole('radiogroup')).toBeNull()
    expect(b().count('symbol')).toBe(0)
  })

  it('is absent in a family render: no markers, no flag, no note, nothing read', async () => {
    await mount({ readOnly: true, openItemId: 1 })
    await act(async () => {})
    expect(document.querySelector('[data-mark],[data-note]')).toBeNull()
    expect(screen.queryByRole('radiogroup', { name: 'Flag' })).toBeNull()
    expect(screen.queryByRole('textbox', { name: 'Note' })).toBeNull()
    expect(screen.queryByText(PRIVATE_WORDS)).toBeNull()
    expect(b().count('symbol')).toBe(0)
  })
})

describe('the symbol sheet', () => {
  it('offers the five flags and None, each 48 dp with a glyph and its name', async () => {
    await mount({ openItemId: 1 })
    const group = await screen.findByRole('radiogroup', { name: 'Flag' })
    const radios = within(group).getAllByRole('radio')
    expect(radios.map((radio) => radio.closest('label')?.textContent)).toEqual([
      'Watch',
      'Breakout',
      'Results',
      'Long term',
      'Avoid',
      'None',
    ])
    for (const radio of radios) {
      expect(radio.closest('label')?.className).toContain('min-h-12')
      expect(radio.closest('label')?.querySelector('svg')).not.toBeNull()
    }
    expect(within(group).getByRole('radio', { name: 'Breakout' })).toBeChecked()
  })

  it('sends mark with the version it read when a flag is picked, then shows what is held', async () => {
    const user = userEvent.setup()
    await mount({ openItemId: 1 })
    await user.click(await screen.findByRole('radio', { name: 'Avoid' }))
    await waitFor(() => expect(b().count('mark')).toBe(1))
    expect(b().calls.find((c) => c.op === 'mark')?.body).toEqual({
      k: 'NSE:RELIANCE',
      flag: 5,
      version: 3,
    })
    await waitFor(() => expect(screen.getByRole('radio', { name: 'Avoid' })).toBeChecked())
    // The next write names the version the first one made.
    await user.click(screen.getByRole('radio', { name: 'None' }))
    await waitFor(() => expect(b().count('mark')).toBe(2))
    expect(b().calls.filter((c) => c.op === 'mark')[1].body).toEqual({
      k: 'NSE:RELIANCE',
      flag: null,
      version: 4,
    })
  })

  // Review L7: a second flag tap while the first is being written would carry the old version.
  it('takes one flag write at a time, the picker held while it is out', async () => {
    let answer: () => void = () => {}
    b().on('mark', (call) => {
      const body = call.body as { flag?: number | null }
      return new Promise((resolve) => {
        answer = () => {
          server.info = {
            ...server.info,
            flag: body.flag ?? null,
            version: (server.info.version ?? 0) + 1,
          }
          resolve({ version: String(server.info.version), id: null, words: null })
        }
      })
    })
    const user = userEvent.setup()
    await mount({ openItemId: 1 })
    await user.click(await screen.findByRole('radio', { name: 'Avoid' }))
    await waitFor(() => expect(b().count('mark')).toBe(1))
    expect(screen.getByRole('radio', { name: 'None' })).toBeDisabled()
    fireEvent.click(screen.getByRole('radio', { name: 'None' }))
    await act(async () => {})
    expect(b().count('mark')).toBe(1)
    await act(async () => answer())
    await waitFor(() => expect(screen.getByRole('radio', { name: 'None' })).toBeEnabled())
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('takes no tap for the first half second after it opens (the open guard)', async () => {
    OPEN_GUARD.ms = 60_000
    await mount({ openItemId: 1 })
    const radio = await screen.findByRole('radio', { name: 'Avoid' })
    expect(document.querySelector('[data-settling]')).not.toBeNull()
    fireEvent.pointerDown(radio)
    fireEvent.mouseDown(radio)
    fireEvent.pointerUp(radio)
    fireEvent.mouseUp(radio)
    fireEvent.click(radio)
    await act(async () => {})
    expect(b().count('mark')).toBe(0)
  })

  it('counts the note, refuses a 501st character before sending, and saves 500', async () => {
    const user = userEvent.setup()
    await mount({ openItemId: 1 })
    const field = await screen.findByRole('textbox', { name: 'Note' })
    expect(screen.getByText('19 of 500')).toBeInTheDocument()
    fireEvent.change(field, { target: { value: 'x'.repeat(501) } })
    expect(screen.getByText('501 of 500')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(NOTE_LONG)
    expect(b().count('mark')).toBe(0)

    fireEvent.change(field, { target: { value: 'y'.repeat(500) } })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(b().count('mark')).toBe(1))
    expect(b().calls.find((c) => c.op === 'mark')?.body).toEqual({
      k: 'NSE:RELIANCE',
      note: 'y'.repeat(500),
      version: 3,
    })
    expect(await screen.findByText('Saved')).toBeInTheDocument()
  })

  it('shows its words on a conflict and reads the symbol again', async () => {
    const user = userEvent.setup()
    await mount({ openItemId: 1 })
    b().on('mark', () => ({ status: 'error', code: 'conflict', message: CONFLICT }))
    const reads = b().count('symbol')
    await user.click(await screen.findByRole('radio', { name: 'Results' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(CONFLICT)
    expect(screen.getByRole('alert').textContent).not.toMatch(/40\d|409|conflict/i)
    await waitFor(() => expect(b().count('symbol')).toBeGreaterThan(reads))
  })

  it('names the lists that hold it, and says Expired with a clock for a gone contract', async () => {
    server.info = info({ expired: true })
    await mount({ openItemId: 1 })
    expect(await screen.findByText('Swing, Momentum, NIFTY 50 (built-in)')).toBeInTheDocument()
    const expired = document.querySelector('[data-expired="true"]') as HTMLElement
    expect(expired).toHaveTextContent('Expired')
    expect(expired.querySelector('svg')).not.toBeNull()
    expect(screen.getByText(PRIVATE_WORDS)).toBeInTheDocument()
  })
})

// SUDARSHAN M8.6 (GA10.8): the owner's default is to save a note typed in the sheet when the
// sheet closes, with one quiet line; an empty or unchanged note writes nothing.
describe('a note typed and not saved when the sheet closes', () => {
  async function openSheet() {
    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <ClosablePage />
        </MemoryRouter>
      </QueryClientProvider>
    )
    await waitFor(() => expect(b().count('view')).toBeGreaterThan(0))
    return screen.findByRole('textbox', { name: 'Note' })
  }

  async function closeSheet() {
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Note' })).toBeNull())
    await act(async () => {})
  }

  it('is saved, once, on the version it was typed on, and says so in one quiet line', async () => {
    const field = await openSheet()
    fireEvent.change(field, { target: { value: '  Buy the retest above 1,420  ' } })
    await closeSheet()
    await waitFor(() => expect(b().count('mark')).toBe(1))
    expect(b().calls.find((c) => c.op === 'mark')?.body).toEqual({
      k: 'NSE:RELIANCE',
      note: 'Buy the retest above 1,420',
      version: 3,
    })
    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith(NOTE_SAVED, undefined, { duration: 2_000 })
    )
    expect(toast.error).not.toHaveBeenCalled()
  })

  it.each([
    ['unchanged', 'Wait for the retest'],
    ['unchanged but for spaces', '  Wait for the retest '],
    ['emptied', ''],
    ['only spaces', '   '],
  ])('writes nothing when %s', async (_how, value) => {
    const field = await openSheet()
    fireEvent.change(field, { target: { value } })
    await closeSheet()
    expect(b().count('mark')).toBe(0)
    expect(toast.info).not.toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('sends nothing over 500 characters, and says why and what to do', async () => {
    const field = await openSheet()
    fireEvent.change(field, { target: { value: 'z'.repeat(501) } })
    await closeSheet()
    expect(b().count('mark')).toBe(0)
    expect(toast.error).toHaveBeenCalledTimes(1)
    expect(toast.error.mock.calls[0][0]).toMatch(new RegExp(`^${NOTE_NOT_SAVED} It is over 500`))
  })

  it("says the server's refusal in its words, never a code", async () => {
    b().on('mark', () => ({ status: 'error', code: 'conflict', message: CONFLICT }))
    const field = await openSheet()
    fireEvent.change(field, { target: { value: 'Exit below 1,380' } })
    await closeSheet()
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    const said = toast.error.mock.calls[0][0] as string
    expect(said).toBe(`${NOTE_NOT_SAVED} ${CONFLICT} ${NOTE_KEPT}`)
    expect(said).not.toMatch(/40\d|conflict/)
    expect(toast.info).not.toHaveBeenCalled()
  })

  // v0.16.0 phone review L2: what the server did not take is kept for the symbol's sheet.
  async function reopen() {
    fireEvent.click(row('RELIANCE').querySelector('button[aria-haspopup="dialog"]') as HTMLElement)
    return screen.findByRole('textbox', { name: 'Note' })
  }

  it('opens the sheet again with a note the server refused, ready to save', async () => {
    b().on('mark', () => ({ status: 'error', code: 'conflict', message: CONFLICT }))
    const field = await openSheet()
    fireEvent.change(field, { target: { value: 'Exit below 1,380' } })
    await closeSheet()
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    const again = await reopen()
    expect(again).toHaveValue('Exit below 1,380')
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
    // Closed again, it is saved as any typed note is, on the version read since.
    b().on('mark', () => ({ version: '5', id: null, words: null }))
    await closeSheet()
    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith(NOTE_SAVED, undefined, { duration: 2_000 })
    )
    expect(
      b()
        .calls.filter((c) => c.op === 'mark')
        .map((c) => c.body)
    ).toEqual([
      { k: 'NSE:RELIANCE', note: 'Exit below 1,380', version: 3 },
      { k: 'NSE:RELIANCE', note: 'Exit below 1,380', version: 3 },
    ])
  })

  it('says the answer of a Save still on its way when the sheet closed, and keeps a refused note', async () => {
    let answer: (value: unknown) => void = () => {}
    b().on(
      'mark',
      () =>
        new Promise((resolve) => {
          answer = resolve
        })
    )
    const user = userEvent.setup()
    const field = await openSheet()
    fireEvent.change(field, { target: { value: 'Typed, then Back' } })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(b().count('mark')).toBe(1))
    await closeSheet()
    await act(async () => answer({ status: 'error', code: 'conflict', message: CONFLICT }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(toast.error.mock.calls[0][0]).toBe(`${NOTE_NOT_SAVED} ${CONFLICT} ${NOTE_KEPT}`)
    expect(b().count('mark'), 'one write, never a second on closing').toBe(1)
    expect(await reopen()).toHaveValue('Typed, then Back')
    b().on('mark', () => ({ version: '5', id: null, words: null }))
    await closeSheet()
    await waitFor(() => expect(toast.info).toHaveBeenCalledTimes(1))
    expect(b().count('mark')).toBe(2)
  })

  it('says Note saved when a Save still on its way when the sheet closed is kept', async () => {
    let answer: (value: unknown) => void = () => {}
    b().on(
      'mark',
      () =>
        new Promise((resolve) => {
          answer = resolve
        })
    )
    const user = userEvent.setup()
    const field = await openSheet()
    fireEvent.change(field, { target: { value: 'Typed, then Back' } })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(b().count('mark')).toBe(1))
    await closeSheet()
    await act(async () => answer({ version: '4', id: null, words: null }))
    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith(NOTE_SAVED, undefined, { duration: 2_000 })
    )
    expect(toast.error).not.toHaveBeenCalled()
    expect(b().count('mark')).toBe(1)
  })

  it('lets a kept note go once the sheet has shown it: emptied and closed, it is not back', async () => {
    b().on('mark', () => ({ status: 'error', code: 'conflict', message: CONFLICT }))
    const field = await openSheet()
    fireEvent.change(field, { target: { value: 'Exit below 1,380' } })
    await closeSheet()
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    fireEvent.change(await reopen(), { target: { value: '' } })
    await closeSheet()
    expect(b().count('mark'), 'an emptied note writes nothing').toBe(1)
    expect(await reopen()).toHaveValue('Wait for the retest')
  })

  it('is not sent twice when Save was pressed before closing', async () => {
    const user = userEvent.setup()
    const field = await openSheet()
    fireEvent.change(field, { target: { value: 'Trail at the 20 EMA' } })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(b().count('mark')).toBe(1))
    await screen.findByText('Saved')
    await closeSheet()
    expect(b().count('mark')).toBe(1)
    expect(toast.info).not.toHaveBeenCalled()
  })
})
