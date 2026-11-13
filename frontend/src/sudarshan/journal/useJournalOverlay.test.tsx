/** What /journal has open lives in the address: Back closes a sheet first, a reload never reopens a form. */
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'
import { describe, expect, it } from 'vitest'
import { journalOverlayOf, modeOf, useJournalOverlay, viewOf } from './useJournalOverlay'

describe('the address', () => {
  it('reads the part, the mode and the sheets, and ignores what it does not know', () => {
    expect(viewOf(new URLSearchParams(''))).toBe('trades')
    expect(viewOf(new URLSearchParams('view=analytics'))).toBe('analytics')
    expect(viewOf(new URLSearchParams('view=nope'))).toBe('trades')
    expect(modeOf(new URLSearchParams('mode=sandbox'))).toBe('sandbox')
    expect(modeOf(new URLSearchParams('mode=paper'))).toBe('live')
    expect(journalOverlayOf(new URLSearchParams('trade=12'))).toEqual({
      kind: 'trade',
      tradeId: 12,
    })
    expect(journalOverlayOf(new URLSearchParams('trade=0'))).toBeNull()
    expect(journalOverlayOf(new URLSearchParams('trade=1.5'))).toBeNull()
    expect(journalOverlayOf(new URLSearchParams('sheet=playbook&playbook=3'))).toEqual({
      kind: 'playbook',
      playbookId: 3,
    })
    expect(journalOverlayOf(new URLSearchParams('sheet=playbook'))).toBeNull()
    expect(journalOverlayOf(new URLSearchParams('sheet=new-playbook'))).toEqual({
      kind: 'playbook',
      playbookId: null,
    })
    expect(journalOverlayOf(new URLSearchParams('sheet=order'))).toBeNull()
  })
})

function Probe() {
  const { overlay, view, mode, open, close, setView, setMode } = useJournalOverlay()
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <div>
      <p data-testid="at">{`${location.pathname}${location.search}`}</p>
      <p data-testid="shown">{`${view}|${mode}|${overlay ? overlay.kind : 'none'}`}</p>
      <button type="button" onClick={() => open({ kind: 'trade', tradeId: 5 })}>
        trade 5
      </button>
      <button type="button" onClick={() => open({ kind: 'playbook', playbookId: null })}>
        new playbook
      </button>
      <button type="button" onClick={close}>
        close
      </button>
      <button type="button" onClick={() => setView('analytics')}>
        analytics
      </button>
      <button type="button" onClick={() => setMode('sandbox')}>
        sandbox
      </button>
      <button type="button" onClick={() => navigate(-1)}>
        back
      </button>
    </div>
  )
}

function renderAt(entries: Parameters<typeof MemoryRouter>[0]['initialEntries']) {
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={(entries?.length ?? 1) - 1}>
      <Routes>
        <Route path="/journal" element={<Probe />} />
        <Route path="/dashboard" element={<p>Dashboard page</p>} />
      </Routes>
    </MemoryRouter>
  )
}

const at = () => screen.getByTestId('at').textContent
const shown = () => screen.getByTestId('shown').textContent

describe('useJournalOverlay', () => {
  it('opens a sheet with one history entry, and Back closes it before it leaves the page', async () => {
    const user = userEvent.setup()
    renderAt(['/dashboard', '/journal'])
    await user.click(screen.getByText('trade 5'))
    expect(at()).toBe('/journal?trade=5')
    expect(shown()).toBe('trades|live|trade')
    await user.click(screen.getByText('back'))
    expect(at()).toBe('/journal')
    await user.click(screen.getByText('back'))
    expect(screen.getByText('Dashboard page')).toBeTruthy()
  })

  it('closing a sheet it pushed goes back, so the close button and Back leave the same history', async () => {
    const user = userEvent.setup()
    renderAt(['/dashboard', '/journal'])
    await user.click(screen.getByText('trade 5'))
    await user.click(screen.getByText('close'))
    expect(at()).toBe('/journal')
    await user.click(screen.getByText('back'))
    expect(screen.getByText('Dashboard page')).toBeTruthy()
  })

  it('closes a sheet opened by a link by replacing the entry, never leaving the app', async () => {
    const user = userEvent.setup()
    renderAt(['/journal?trade=9'])
    expect(shown()).toBe('trades|live|trade')
    await user.click(screen.getByText('close'))
    expect(at()).toBe('/journal')
  })

  it('a double tap on close goes back once', async () => {
    const user = userEvent.setup()
    renderAt(['/dashboard', '/journal'])
    await user.click(screen.getByText('trade 5'))
    const close = screen.getByText('close')
    await act(async () => {
      close.click()
      close.click()
    })
    expect(at()).toBe('/journal')
    expect(screen.queryByText('Dashboard page')).toBeNull()
  })

  it('opening over a sheet takes its place and its mark', async () => {
    const user = userEvent.setup()
    renderAt(['/dashboard', '/journal'])
    await user.click(screen.getByText('trade 5'))
    await user.click(screen.getByText('new playbook'))
    expect(at()).toBe('/journal?sheet=new-playbook')
    await user.click(screen.getByText('back'))
    expect(at()).toBe('/journal')
  })

  it('moves between parts and modes by replacing the entry, keeping the rest of the address', async () => {
    const user = userEvent.setup()
    renderAt(['/dashboard', '/journal'])
    await user.click(screen.getByText('analytics'))
    await user.click(screen.getByText('sandbox'))
    expect(at()).toBe('/journal?view=analytics&mode=sandbox')
    await user.click(screen.getByText('back'))
    expect(screen.getByText('Dashboard page')).toBeTruthy()
  })

  it('a reload never reopens a form, but a link to a trade opens it', async () => {
    renderAt([
      '/dashboard',
      { pathname: '/journal', search: '?sheet=new-playbook', state: { sdOverlay: true } },
    ])
    await act(async () => {})
    expect(shown()).toBe('trades|live|none')
  })
})
