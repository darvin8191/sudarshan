/**
 * v0.17.0 phone review L1 (M7.36): at 320 px with Larger text the Calendar's "Watchlists" ran past
 * its segment. jsdom lays nothing out, so what keeps a word inside its segment is pinned here;
 * e2e/sd-calendar.spec.ts measures it at 320 x 640, 360 x 800 and 800 x 360 at both text sizes.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Segmented } from './Segmented'

const SHOWS = [
  { id: 'holdings', label: 'Holdings' },
  { id: 'watchlists', label: 'Watchlists' },
  { id: 'all', label: 'All' },
] as const

describe('a segmented control (L1)', () => {
  it('shares the width while the words fit and gives a longer word its room', () => {
    render(<Segmented label="Show the events of" value="all" options={SHOWS} onChange={() => {}} />)
    const list = screen.getByRole('tablist', { name: 'Show the events of' })
    expect(list.style.gridTemplateColumns).toBe('repeat(3, minmax(min-content, 1fr))')
    for (const tab of screen.getAllByRole('tab')) expect(tab).toHaveClass('sd-seg', 'min-h-12')
    expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'true')
  })
})
