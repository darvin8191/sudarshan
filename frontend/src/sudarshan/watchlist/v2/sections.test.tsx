import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

import { createQueryClient } from '@/app/providers'
import { ListTabs } from '../ListTabs'
import { OPEN_GUARD } from '../openGuard'
import { rowView } from '../rows'
import { SectionSheets } from '../SectionMenus'
import type { LiveRow } from '../useLiveRows'
import { useOverlay } from '../useOverlay'
import { WatchlistRows } from '../WatchlistRows'
import { COLLAPSED_KEY, groupsOf, NAME_LONG, SECTIONS_FULL, useCollapsed } from './sections'
import type { Row, Section, View } from './types'
import { useListView } from './useListView'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const SYMBOLS: Record<number, string> = { 1: 'AAA', 2: 'BBB', 3: 'CCC', 4: 'DDD', 5: 'EEE' }
const liveRow = (id: number): LiveRow => ({
  item: { id, symbol: SYMBOLS[id], exchange: 'NSE', position: id },
  key: `NSE:${SYMBOLS[id]}`,
  view: rowView(undefined, undefined),
  direction: null,
  stale: false,
  tickAt: null,
})
// The lists API's own order is not the view's: the page must show the view's.
const ROWS = [2, 1, 3, 4, 5].map(liveRow)

const row = (item: number, section_id: number | null): Row => ({
  item,
  contract: {
    exchange: 'NSE',
    symbol: SYMBOLS[item],
    key: `NSE:${SYMBOLS[item]}`,
    name: SYMBOLS[item],
    lot: null,
    tick: null,
    expiry: null,
    isin: null,
  },
  section_id,
  flag: null,
  has_note: false,
  expired: false,
  metric: null,
})
const section = (
  id: number,
  name: string,
  mark: number,
  position: number,
  count: number
): Section => ({
  id,
  name,
  mark,
  position,
  count,
})
const view = (over: Partial<View> = {}): View => ({
  ref: 'list:1',
  kind: 'list',
  name: 'Mine',
  version: 'a:7',
  locked: false,
  as_of: null,
  lines: [],
  total: 5,
  missing: [],
  sections: [section(11, 'IT', 0, 2, 1), section(10, 'Banks', 1, 1, 2)],
  rows: [row(2, 10), row(3, 10), row(4, 11), row(1, null), row(5, null)],
  follow: [],
  pref: { sort: 'manual', descending: false, basis: 'day', held: false } as View['pref'],
  ...over,
})

/** The page's wiring of the rows, the sheets and the list menu's entry, without the rest of the page. */
function Harness({ withSections = true }: { withSections?: boolean }) {
  const data = useListView('list:1').data
  const folds = useCollapsed('list:1')
  const { overlay, open, swap, close } = useOverlay(true)
  const where = useLocation()
  const layout = data
    ? {
        groups: groupsOf(data),
        collapsed: folds.collapsed,
        onToggle: folds.toggle,
        onMenu: (sectionId: number) => open({ kind: 'section', sectionId }),
      }
    : undefined
  return (
    <>
      <output data-testid="where">{where.search}</output>
      <button type="button" onClick={() => open({ kind: 'sectionName', sectionId: null })}>
        New section
      </button>
      <WatchlistRows
        rows={ROWS}
        loading={false}
        editing={false}
        onOpen={() => {}}
        onMove={() => {}}
        onRemove={() => {}}
        sections={withSections ? layout : undefined}
      />
      <SectionSheets
        overlay={overlay}
        view={data}
        listId={1}
        alertFor={(ref, name, count) => ({
          href: `/m?${ref}${name}${count}`,
          off: null,
          go: () => {},
        })}
        onSwap={swap}
        onClose={close}
      />
    </>
  )
}

function setup(data: View = view(), path = '/watchlist') {
  b().on('state', () => ({ enabled: true }))
  b().on('view', () => data)
  b().on('section_add', () => ({ version: 'a:8' }))
  b().on('section_edit', () => ({ version: 'a:8' }))
  b().on('section_remove', () => ({ version: 'a:8' }))
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter
        initialEntries={['/watchlist', path]}
        initialIndex={path === '/watchlist' ? 0 : 1}
      >
        <Harness />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const shown = () =>
  screen.getAllByRole('listitem').map((li) => li.textContent?.replace(/\s+/g, ' ').trim() ?? '')

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  localStorage.clear()
})
afterEach(() => vi.restoreAllMocks())

describe('the rows in sections', () => {
  it('shows no section first, then each section by position, under its header', async () => {
    setup()
    await screen.findByText('Banks · 2')
    const lines = shown()
    expect(lines.map((line) => line.slice(0, 3))).toEqual([
      'AAA',
      'EEE',
      'Ban',
      'BBB',
      'CCC',
      'IT ',
      'DDD',
    ])
  })

  it('says each header by name, count, glyph and a stripe; none for mark 0', async () => {
    setup()
    const banks = (await screen.findByText('Banks · 2')).closest('li') as HTMLElement
    expect(banks.querySelector('svg[data-shape="circle"]')).not.toBeNull()
    expect(banks.querySelector('[data-stripe="1"]')).not.toBeNull()
    const it = screen.getByText('IT · 1').closest('li') as HTMLElement
    expect(it.querySelector('svg[data-shape]')).toBeNull()
    expect(it.querySelector('[data-stripe]')).toBeNull()
  })

  it('folds and unfolds with a chevron named Collapse or Expand, and remembers it on this device', async () => {
    const user = userEvent.setup()
    const { unmount } = setup()
    const toggle = await screen.findByRole('button', { name: /Banks · 2.*Collapse/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await user.click(toggle)
    expect(screen.queryByText('BBB')).toBeNull()
    expect(screen.getByRole('button', { name: /Banks · 2.*Expand/ })).toHaveAttribute(
      'aria-expanded',
      'false'
    )
    expect(JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? '{}')).toEqual({ 'list:1': [10] })
    unmount()
    setup()
    await screen.findByText('Banks · 2')
    expect(screen.queryByText('BBB')).toBeNull()
    expect(screen.getByText('DDD')).toBeInTheDocument()
  })

  it('keeps working when the device refuses its storage', async () => {
    const user = userEvent.setup()
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('refused')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('refused')
    })
    setup()
    await user.click(await screen.findByRole('button', { name: /Banks · 2.*Collapse/ }))
    expect(screen.queryByText('BBB')).toBeNull()
    await user.click(screen.getByRole('button', { name: /Banks · 2.*Expand/ }))
    expect(screen.getByText('BBB')).toBeInTheDocument()
  })

  it("draws today's flat rows when no sections are given (the switch off)", () => {
    render(
      <WatchlistRows
        rows={ROWS}
        loading={false}
        editing={false}
        onOpen={() => {}}
        onMove={() => {}}
        onRemove={() => {}}
      />
    )
    expect(shown().map((line) => line.slice(0, 3))).toEqual(['BBB', 'AAA', 'CCC', 'DDD', 'EEE'])
    expect(screen.queryByRole('button', { name: /Collapse|Expand/ })).toBeNull()
  })
})

describe('the section menu and its writes', () => {
  it('sends the version the view answered with a mark change, then reads the list again', async () => {
    const user = userEvent.setup()
    setup()
    await user.click(await screen.findByRole('button', { name: 'Options for Banks' }))
    expect(screen.getByTestId('where')).toHaveTextContent('sheet=section&section=10')
    const sheet = screen.getByRole('dialog')
    for (const name of ['None', 'Circle', 'Square', 'Triangle', 'Diamond', 'Star']) {
      expect(within(sheet).getByRole('button', { name })).toBeInTheDocument()
    }
    await user.click(within(sheet).getByRole('button', { name: 'Square' }))
    await waitFor(() => expect(b().count('section_edit')).toBe(1))
    expect(b().calls.find((call) => call.op === 'section_edit')?.body).toEqual({
      id: 10,
      mark: 2,
      version: 'a:7',
    })
    await waitFor(() => expect(b().count('view')).toBe(2))
  })

  it('adds a section and removes one, each with the held version', async () => {
    const user = userEvent.setup()
    setup()
    await user.click(await screen.findByRole('button', { name: 'New section' }))
    await user.type(screen.getByLabelText('Section name'), 'Metals')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(b().count('section_add')).toBe(1))
    expect(b().calls.find((call) => call.op === 'section_add')?.body).toEqual({
      list: 1,
      name: 'Metals',
      version: 'a:7',
    })
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^$/))

    await user.click(screen.getByRole('button', { name: 'Options for Banks' }))
    await user.click(screen.getByRole('button', { name: 'Remove section' }))
    expect(
      screen.getByText(
        'Remove the section Banks? Its 2 symbols stay in the list, under No section.'
      )
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(b().count('section_remove')).toBe(1))
    expect(b().calls.find((call) => call.op === 'section_remove')?.params).toEqual({
      version: 'a:7',
    })
  })

  it("shows a conflict in the server's words and reads the list again, then writes with the new version", async () => {
    const user = userEvent.setup()
    setup()
    const words = 'Changed on another screen. The list is shown again.'
    let first = true
    b().on('section_edit', () => {
      if (first) {
        first = false
        return { status: 'error', code: 'conflict', message: words }
      }
      return { version: 'a:9' }
    })
    b().on('view', () => view({ version: b().count('view') > 1 ? 'a:8' : 'a:7' }))
    await user.click(await screen.findByRole('button', { name: 'Options for Banks' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Star' }))
    expect(await screen.findByText(words)).toBeInTheDocument()
    expect(screen.queryByText(/409|conflict/i)).toBeNull()
    await waitFor(() => expect(b().count('view')).toBe(2))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Star' }))
    await waitFor(() => expect(b().count('section_edit')).toBe(2))
    expect(b().calls.filter((call) => call.op === 'section_edit')[1].body).toMatchObject({
      version: 'a:8',
    })
  })

  it('refuses a 41-letter name before sending, in words', async () => {
    const user = userEvent.setup()
    setup()
    await user.click(await screen.findByRole('button', { name: 'New section' }))
    await user.type(screen.getByLabelText('Section name'), 'x'.repeat(41))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent(NAME_LONG)
    expect(NAME_LONG).toBe('Section names can be up to 40 letters.')
    expect(b().count('section_add')).toBe(0)
  })

  it('refuses a 21st section before sending, and shows the same words when the server refuses', async () => {
    const user = userEvent.setup()
    const twenty = Array.from({ length: 20 }, (_, i) => section(100 + i, `S${i}`, 0, i + 1, 0))
    setup(view({ sections: twenty, rows: [] }))
    await user.click(await screen.findByRole('button', { name: 'New section' }))
    await user.type(screen.getByLabelText('Section name'), 'One more')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent(SECTIONS_FULL)
    expect(SECTIONS_FULL).toBe('A list can have 20 sections.')
    expect(b().count('section_add')).toBe(0)
  })

  it('shows the 20-section words when the server refuses a section the page thought fit', async () => {
    const user = userEvent.setup()
    setup()
    b().on('section_add', () => ({
      status: 'error',
      code: 'limit',
      message: 'Too many sections for one list.',
    }))
    await user.click(await screen.findByRole('button', { name: 'New section' }))
    await user.type(screen.getByLabelText('Section name'), 'Metals')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(SECTIONS_FULL)
  })

  it('closes the sheet of a section that is gone after the list is read again', async () => {
    const user = userEvent.setup()
    setup()
    await user.click(await screen.findByRole('button', { name: 'Options for IT' }))
    b().on('view', () => view({ sections: [section(10, 'Banks', 1, 1, 2)], version: 'a:8' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Star' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})

describe('48 dp and the open guard', () => {
  it('gives every control of a header and of the sheets at least 48 dp', async () => {
    const user = userEvent.setup()
    setup()
    const header = (await screen.findByText('Banks · 2')).closest('li') as HTMLElement
    for (const control of within(header).getAllByRole('button')) {
      expect(control.className).toMatch(/\b(min-h-12|size-12)\b/)
    }
    await user.click(within(header).getByRole('button', { name: 'Options for Banks' }))
    const sheet = screen.getByRole('dialog')
    for (const name of ['Rename', 'None', 'Circle', 'Star', 'Remove section', 'Close']) {
      expect(within(sheet).getByRole('button', { name }).className).toMatch(
        /\b(min-h-12|size-12)\b/
      )
    }
  })

  it('ignores a tap on the menu for half a second after it opens, and Back closes it first', async () => {
    OPEN_GUARD.ms = 500
    const user = userEvent.setup()
    setup()
    await user.click(await screen.findByRole('button', { name: 'Options for Banks' }))
    const rename = within(screen.getByRole('dialog')).getByRole('button', { name: 'Rename' })
    fireEvent.click(rename)
    expect(screen.getByTestId('where')).toHaveTextContent('sheet=section&section=10')
    await new Promise((done) => setTimeout(done, 550))
    fireEvent.click(rename)
    await waitFor(() =>
      expect(screen.getByTestId('where')).toHaveTextContent('sheet=section-name&section=10')
    )
  })
})

describe('the list menu', () => {
  const lists = [{ id: 1, name: 'Mine', position: 0, items: [] }]
  const menu = async (props: { onNewSection?: () => void; newSectionOff?: string | null }) => {
    const user = userEvent.setup()
    render(
      <ListTabs
        lists={lists}
        activeId={1}
        editing={false}
        onSelect={() => {}}
        onCreate={() => {}}
        onRename={() => {}}
        onEdit={() => {}}
        onDelete={() => {}}
        strip={<div />}
        {...props}
      />
    )
    await user.click(screen.getByRole('button', { name: 'List options' }))
    return user
  }

  it('offers New section only for an own list, and says why it is off at 20', async () => {
    const onNewSection = vi.fn()
    const user = await menu({ onNewSection })
    await user.click(await screen.findByRole('menuitem', { name: 'New section' }))
    expect(onNewSection).toHaveBeenCalledOnce()
  })

  it('offers none of it for a built-in list', async () => {
    await menu({})
    expect(screen.queryByRole('menuitem', { name: 'New section' })).toBeNull()
  })

  it('turns it off at 20 sections, with the words', async () => {
    await menu({ onNewSection: () => {}, newSectionOff: SECTIONS_FULL })
    expect(await screen.findByRole('menuitem', { name: 'New section' })).toHaveAttribute(
      'aria-disabled',
      'true'
    )
    expect(screen.getByText(SECTIONS_FULL)).toBeInTheDocument()
  })
})
