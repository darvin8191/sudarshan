/**
 * The phone books' history (M6.9): what a reload, Back, Forward and a quick
 * second tap leave open. A probe page drives the hook the books share; each
 * case is one a trader can meet on Android Chrome.
 */
import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import type { BookOverlay, useBookOverlay as Hook } from './useBookOverlay'
import { useBookOverlay } from './useBookOverlay'

type Entry = string | { pathname: string; search?: string; state?: unknown }

const nav = { go: (_delta: number) => {} }
const seen = { state: null as unknown }

function Probe({ hook }: { hook: typeof Hook }) {
  const { overlay, open, swap, close } = hook()
  const navigate = useNavigate()
  nav.go = (delta) => navigate(delta)
  const location = useLocation()
  seen.state = location.state
  const show = (next: BookOverlay) => () => open(next)
  return (
    <div>
      <p data-testid="overlay">{overlay ? `${overlay.act} ${overlay.row ?? ''}`.trim() : 'none'}</p>
      <p data-testid="where">{`${location.pathname}${location.search}`}</p>
      <button type="button" onClick={show({ act: 'sheet', row: 'A' })}>
        Sheet A
      </button>
      <button type="button" onClick={show({ act: 'sheet', row: 'B' })}>
        Sheet B
      </button>
      <button type="button" onClick={() => swap({ act: 'exit', row: 'A' })}>
        Exit A
      </button>
      <button type="button" onClick={show({ act: 'exitall', row: null })}>
        Exit all
      </button>
      <button type="button" onClick={close}>
        Close
      </button>
    </div>
  )
}

function renderProbe(entries: Entry[] = ['/dashboard', '/positions'], hook = useBookOverlay) {
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
      <Routes>
        <Route path="/positions" element={<Probe hook={hook} />} />
        <Route path="/dashboard" element={<p>Dashboard page</p>} />
      </Routes>
    </MemoryRouter>
  )
}

const overlay = () => screen.queryByTestId('overlay')?.textContent
const where = () => screen.queryByTestId('where')?.textContent ?? 'left the page'
const leftThePage = () => screen.queryByText('Dashboard page') !== null
const tap = (name: string) => screen.getByRole('button', { name })
const back = () => act(() => nav.go(-1))
const forward = () => act(() => nav.go(1))

async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve()
  })
}

describe('a step and a reload', () => {
  it('a confirmation open when the tab was reloaded does not open again by itself', async () => {
    // Load 1: the trader opens "Exit all", and the tab is discarded with it open.
    vi.resetModules()
    const first = await import('./useBookOverlay')
    const before = renderProbe(undefined, first.useBookOverlay)
    act(() => tap('Exit all').click())
    expect(overlay()).toBe('exitall')
    const restored = { pathname: '/positions', search: '?act=exitall', state: seen.state }
    before.unmount()

    // Load 2: Chrome brings the tab back on that entry, its state intact, with
    // the module (and every counter in it) loaded afresh, and the cards'
    // entry under it.
    vi.resetModules()
    const second = await import('./useBookOverlay')
    renderProbe(['/dashboard', '/positions', restored], second.useBookOverlay)
    await settle()
    expect(overlay()).toBe('none')
    expect(where()).toBe('/positions')
    // The page went back to its cards: the next Back leaves the page.
    back()
    expect(leftThePage()).toBe(true)
  })

  it('a step restored with an entry from before this page was loaded is not shown', async () => {
    // The review's probe: the state a load before this one wrote.
    renderProbe([
      '/dashboard',
      '/positions',
      { pathname: '/positions', search: '?act=exitall', state: { sdOverlay: true, sdVisit: 'v1' } },
    ])
    await settle()
    expect(overlay()).toBe('none')
    expect(where()).toBe('/positions')
    back()
    expect(leftThePage()).toBe(true)
  })

  it('a step the page did not push is replaced in place: Back then leaves the page', async () => {
    // A link opened a sheet, and a step was taken from it: the entry is the page's first.
    renderProbe([
      '/dashboard',
      { pathname: '/positions', search: '?act=exit&row=A', state: { sdOverlay: false } },
    ])
    await settle()
    expect(overlay()).toBe('none')
    expect(where()).toBe('/positions')
    back()
    expect(leftThePage()).toBe(true)
  })

  it('goes back once only: a second dead step under the first is replaced, not gone back from', async () => {
    // Going back from both would leave the page, which no Back was pressed for.
    const dead = { pathname: '/positions', search: '?act=exitall', state: { sdOverlay: true } }
    renderProbe(['/dashboard', dead, dead])
    await settle()
    expect(leftThePage()).toBe(false)
    expect(overlay()).toBe('none')
    expect(where()).toBe('/positions')
  })

  it('never goes back from the first entry of the tab: it is replaced', async () => {
    // The browser's router keeps each entry's place in the tab's history.
    window.history.replaceState({ idx: 0 }, '')
    try {
      renderProbe([{ pathname: '/positions', search: '?act=exitall', state: { sdOverlay: true } }])
      await settle()
      expect(overlay()).toBe('none')
      expect(where()).toBe('/positions')
    } finally {
      window.history.replaceState(null, '')
    }
  })

  it('a sheet restored with the page opens again, and closes', async () => {
    renderProbe(['/dashboard', '/positions?row=A'])
    expect(overlay()).toBe('sheet A')
    act(() => tap('Close').click())
    expect(overlay()).toBe('none')
    expect(where()).toBe('/positions')
  })
})

describe('Back and Forward', () => {
  it('a sheet reached again by Forward can still be closed', () => {
    renderProbe()
    act(() => tap('Sheet A').click())
    act(() => tap('Close').click())
    expect(where()).toBe('/positions')

    forward()
    expect(overlay()).toBe('sheet A')
    act(() => tap('Close').click())
    expect(overlay()).toBe('none')
    expect(where()).toBe('/positions')
  })

  it('a confirmation left by Back does not open again on Forward', async () => {
    renderProbe()
    act(() => tap('Sheet A').click())
    act(() => tap('Exit A').click())
    expect(overlay()).toBe('exit A')

    back()
    expect(overlay()).toBe('none')
    forward()
    await settle()
    expect(overlay()).toBe('none')
    expect(where()).toBe('/positions')
    // Back from here leaves the page: no second entry of cards was left behind.
    back()
    expect(leftThePage()).toBe(true)
  })

  it('a confirmation closed by its button does not open again on Forward', async () => {
    renderProbe()
    act(() => tap('Exit all').click())
    act(() => tap('Close').click())
    expect(where()).toBe('/positions')

    forward()
    await settle()
    expect(overlay()).toBe('none')
    expect(where()).toBe('/positions')
    back()
    expect(leftThePage()).toBe(true)
  })
})

describe('two taps in the same moment', () => {
  it('a double tap on a card pushes one entry: one Back closes its sheet', () => {
    renderProbe()
    act(() => {
      tap('Sheet A').click()
      tap('Sheet A').click()
    })
    expect(overlay()).toBe('sheet A')

    back()
    expect(overlay()).toBe('none')
    expect(where()).toBe('/positions')
    back()
    expect(screen.getByText('Dashboard page')).toBeInTheDocument()
  })

  it('two closes in the same moment go back once', () => {
    renderProbe()
    act(() => tap('Sheet A').click())
    act(() => {
      tap('Close').click()
      tap('Close').click()
    })
    expect(where()).toBe('/positions')
  })

  it('a card tapped while a close is going back opens over the cards, once', () => {
    renderProbe()
    act(() => tap('Sheet A').click())
    act(() => {
      tap('Close').click()
      tap('Sheet B').click()
    })
    expect(overlay()).toBe('sheet B')

    back()
    expect(overlay()).toBe('none')
    expect(where()).toBe('/positions')
    back()
    expect(screen.getByText('Dashboard page')).toBeInTheDocument()
  })
})
