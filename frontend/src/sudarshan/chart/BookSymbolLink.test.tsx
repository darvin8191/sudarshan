import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeScreen } from '@/test/screenWidth'
import { BookSymbolLink } from './BookSymbolLink'

const OPTION = 'NIFTY27OCT2625000CE'

function renderLink(wide?: 'terminal' | 'text') {
  return render(
    <MemoryRouter>
      <BookSymbolLink symbol={OPTION} exchange="NFO" wide={wide} />
    </MemoryRouter>
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('BookSymbolLink', () => {
  it("opens the phone chart on a phone, a full touch target high, drawn as upstream's link", () => {
    fakeScreen('phone')
    renderLink()
    const link = screen.getByRole('link', { name: `Open the chart for ${OPTION} on NFO` })
    expect(link).toHaveAttribute('href', `/chart/NFO/${OPTION}`)
    expect(link).toHaveClass('min-h-12')
    expect(link).toHaveTextContent(OPTION)
    expect(link.querySelector('svg')).not.toBeNull()
  })

  it("keeps upstream's link into the terminal on a wide screen", () => {
    fakeScreen('wide')
    renderLink()
    expect(screen.getByRole('link', { name: `Open chart for ${OPTION} on NFO` })).toHaveAttribute(
      'href',
      `/trading?symbol=${OPTION}&exchange=NFO`
    )
  })

  it('keeps the bare symbol on a wide screen where upstream had no link (holdings)', () => {
    fakeScreen('wide')
    renderLink('text')
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText(OPTION)).toBeInTheDocument()
  })

  it('opens the phone chart on a phone even where upstream had no link', () => {
    fakeScreen('phone')
    renderLink('text')
    expect(screen.getByRole('link')).toHaveAttribute('href', `/chart/NFO/${OPTION}`)
  })

  it('follows the screen as it turns', () => {
    const screenNow = fakeScreen('phone')
    renderLink()
    expect(screen.getByRole('link')).toHaveAttribute('href', `/chart/NFO/${OPTION}`)
    act(() => screenNow.turn('wide'))
    expect(screen.getByRole('link')).toHaveAttribute(
      'href',
      `/trading?symbol=${OPTION}&exchange=NFO`
    )
  })
})
