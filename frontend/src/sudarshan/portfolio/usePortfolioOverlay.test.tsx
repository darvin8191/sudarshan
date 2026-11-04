import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'
import { describe, expect, it } from 'vitest'
import { portfolioOverlayOf, tabOf, usePortfolioOverlay, viewOf } from './usePortfolioOverlay'

const q = (search: string) => new URLSearchParams(search)

describe('what the address holds', () => {
  it('reads each sheet, and ignores one it does not know or one missing its id', () => {
    expect(portfolioOverlayOf(q('sheet=new-tab'))).toEqual({ kind: 'new-tab' })
    expect(portfolioOverlayOf(q('sheet=import&tab=3'))).toEqual({ kind: 'import' })
    expect(portfolioOverlayOf(q('sheet=edit&activity=8'))).toEqual({ kind: 'edit', activityId: 8 })
    expect(portfolioOverlayOf(q('sheet=price&asset=2'))).toEqual({ kind: 'price', assetId: 2 })
    expect(portfolioOverlayOf(q('activity=8'))).toEqual({ kind: 'activity', activityId: 8 })
    expect(portfolioOverlayOf(q('asset=2'))).toEqual({ kind: 'asset', assetId: 2 })
    expect(portfolioOverlayOf(q('sheet=edit'))).toBeNull()
    expect(portfolioOverlayOf(q('sheet=price'))).toBeNull()
    expect(portfolioOverlayOf(q('sheet=nothing'))).toBeNull()
    expect(portfolioOverlayOf(q('activity=-1&asset=abc'))).toBeNull()
  })

  it('the tab is a whole number; none is the Upstox tab', () => {
    expect(tabOf(q(''))).toBeNull()
    expect(tabOf(q('tab=4'))).toBe(4)
    expect(tabOf(q('tab=x'))).toBeNull()
    expect(tabOf(q('tab=0'))).toBeNull()
  })

  it('a view is one of the tab’s own, else its first', () => {
    expect(viewOf(q('view=funds'), null)).toBe('funds')
    expect(viewOf(q('view=funds'), 3)).toBe('overview')
    expect(viewOf(q('view=activity'), 3)).toBe('activity')
    expect(viewOf(q('view=activity'), null)).toBe('holdings')
    expect(viewOf(q(''), 3)).toBe('overview')
  })
})

function Probe() {
  const o = usePortfolioOverlay()
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <div>
      <p data-testid="where">{`${location.pathname}${location.search}`}</p>
      <p data-testid="kind">{o.overlay?.kind ?? 'none'}</p>
      <button type="button" onClick={() => o.open({ kind: 'add' })}>
        open add
      </button>
      <button type="button" onClick={() => o.open({ kind: 'import' })}>
        open import
      </button>
      <button type="button" onClick={() => o.close()}>
        close
      </button>
      <button type="button" onClick={() => o.selectTab(3)}>
        tab 3
      </button>
      <button type="button" onClick={() => o.setView('activity')}>
        activity
      </button>
      <button type="button" onClick={() => navigate(-1)}>
        back
      </button>
    </div>
  )
}

function renderAt(entries: string[], index?: number) {
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={index}>
      <Routes>
        <Route path="/portfolio" element={<Probe />} />
        <Route path="/dashboard" element={<p>dashboard</p>} />
      </Routes>
    </MemoryRouter>
  )
}
const click = (name: string) => act(async () => screen.getByText(name).click())

describe('Back closes a sheet first', () => {
  it('opening pushes an entry; Back and Close both leave the page where it was', async () => {
    renderAt(['/dashboard', '/portfolio?tab=3'], 1)
    await click('open add')
    expect(screen.getByTestId('where').textContent).toBe('/portfolio?tab=3&sheet=add')
    await click('back')
    expect(screen.getByTestId('where').textContent).toBe('/portfolio?tab=3')
    await click('open add')
    await click('close')
    expect(screen.getByTestId('where').textContent).toBe('/portfolio?tab=3')
    await click('back')
    expect(screen.getByText('dashboard')).toBeTruthy()
  })

  it('a sheet swapped for another keeps one entry', async () => {
    renderAt(['/dashboard', '/portfolio'], 1)
    await click('open add')
    await click('open import')
    expect(screen.getByTestId('kind').textContent).toBe('import')
    await click('back')
    expect(screen.getByTestId('kind').textContent).toBe('none')
    expect(screen.getByTestId('where').textContent).toBe('/portfolio')
  })

  it('a sheet the page did not open (a reload) closes in place and never leaves the app', async () => {
    renderAt(['/dashboard', '/portfolio?sheet=add'], 1)
    expect(screen.getByTestId('kind').textContent).toBe('add')
    await click('close')
    expect(screen.getByTestId('where').textContent).toBe('/portfolio')
    expect(screen.queryByText('dashboard')).toBeNull()
  })

  it('closing twice for one entry goes back once', async () => {
    renderAt(['/dashboard', '/portfolio'], 1)
    await click('open add')
    await act(async () => {
      screen.getByText('close').click()
      screen.getByText('close').click()
    })
    expect(screen.getByTestId('where').textContent).toBe('/portfolio')
  })

  it('a move between tabs and views replaces the entry, so Back does not walk through them', async () => {
    renderAt(['/dashboard', '/portfolio'], 1)
    await click('tab 3')
    await click('activity')
    expect(screen.getByTestId('where').textContent).toBe('/portfolio?tab=3&view=activity')
    await click('back')
    expect(screen.getByText('dashboard')).toBeTruthy()
  })
})
