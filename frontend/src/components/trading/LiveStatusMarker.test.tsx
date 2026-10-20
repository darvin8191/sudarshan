import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { LiveStatus } from '@/lib/trading/liveStatus'
import { LiveStatusMarker } from './LiveStatusMarker'

const status = (state: LiveStatus['state']): LiveStatus => ({
  state,
  lastTickAt: Date.UTC(2026, 9, 20, 4, 30, 5),
  lastTickTime: null,
  staleAfterMs: 15_000,
  opensAt: null,
})

describe('LiveStatusMarker', () => {
  it.each([
    ['delayed', 'Delayed'],
    ['stale', 'Stale'],
    ['closed', 'Market closed'],
  ] as const)('marks %s prices with a word and a glyph', (state, label) => {
    const { container } = render(<LiveStatusMarker status={status(state)} />)
    const marker = screen.getByRole('status')
    expect(marker).toHaveTextContent(label)
    expect(marker.getAttribute('data-live-state')).toBe(state)
    expect(container.querySelector('svg')).not.toBeNull()
    expect(marker.getAttribute('title')).toMatch(/\.$/)
  })

  it('tells a screen reader what a stale price means', () => {
    render(<LiveStatusMarker status={status('stale')} />)
    expect(screen.getByRole('status')).toHaveTextContent(
      'Stale. No price update since 10:00:05. The last price shown may be out of date.'
    )
  })

  it('shows nothing while prices are live or still connecting', () => {
    const { container, rerender } = render(<LiveStatusMarker status={status('live')} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<LiveStatusMarker status={status('connecting')} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<LiveStatusMarker status={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})
