import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { feedWords, LiveLine } from './LiveLine'

const NOW = 1_760_000_000_000

describe('LiveLine', () => {
  it('says "Real-time" while ticks arrive, never "Live"', () => {
    render(<LiveLine line={{ kind: 'live' }} now={NOW} />)
    expect(screen.getByText('Real-time')).toBeInTheDocument()
    expect(screen.queryByText(/\blive\b/i)).toBeNull()
  })

  it('says "Delayed" with the age of the figures while a quote request fills in', () => {
    const { container } = render(
      <LiveLine line={{ kind: 'polling', at: NOW - 3_000, stale: false }} now={NOW} />
    )
    expect(container).toHaveTextContent('Delayed · updated 3s ago')
    expect(container.querySelector('[data-feed]')?.getAttribute('data-feed')).toBe('polling')
  })

  it('says "Stale" when the fallback has stopped answering in market hours', () => {
    const { container } = render(
      <LiveLine line={{ kind: 'polling', at: NOW - 25_000, stale: true }} now={NOW} />
    )
    expect(container).toHaveTextContent('Stale · updated 25s ago')
    expect(container.querySelector('[data-feed]')?.getAttribute('data-feed')).toBe('stale')
  })

  it('draws nothing where nothing is held: there is no price to wait for', () => {
    const { container } = render(<LiveLine line={{ kind: 'idle' }} now={NOW} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('keeps the words it already had', () => {
    expect(feedWords({ kind: 'closed' }, NOW)).toBe('Market closed')
    expect(feedWords({ kind: 'waiting' }, NOW)).toBe('Waiting for prices')
    expect(feedWords({ kind: 'delayed', at: NOW - 4_000, stale: false }, NOW)).toBe(
      'Updated 4s ago'
    )
    expect(feedWords({ kind: 'delayed', at: NOW - 40_000, stale: true }, NOW)).toBe(
      'Stale · updated 40s ago'
    )
    expect(feedWords({ kind: 'connecting', at: null, stale: false, again: false }, NOW)).toBe(
      'Connecting'
    )
    expect(feedWords({ kind: 'connecting', at: NOW - 6_000, stale: false, again: true }, NOW)).toBe(
      'Reconnecting · updated 6s ago'
    )
  })
})
