import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LiveStatus } from '@/lib/trading/liveStatus'
import type { FeedLine } from '@/sudarshan/dashboard/live'
import { LiveStatusMarker } from './LiveStatusMarker'

/** 10:00:00 in India. */
const NOW = Date.UTC(2026, 9, 20, 4, 30, 0)

const status = (line: FeedLine): LiveStatus => ({
  line,
  lastTickAt: null,
  lastUpdateAt: null,
  lastTickTime: null,
  staleAfterMs: 15_000,
  opensAt: null,
})

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('LiveStatusMarker', () => {
  it.each([
    [{ kind: 'polling', at: NOW - 3_000, stale: false }, 'Delayed · updated 3s ago', 'polling'],
    [{ kind: 'polling', at: NOW - 20_000, stale: true }, 'Stale · updated 20s ago', 'stale'],
    [
      { kind: 'connecting', at: NOW - 6_000, stale: false, again: true },
      'Reconnecting · updated 6s ago',
      'connecting',
    ],
    [{ kind: 'delayed', at: NOW - 25_000, stale: true }, 'Stale · updated 25s ago', 'stale'],
    [{ kind: 'closed' }, 'Market closed', 'closed'],
  ] as Array<
    [FeedLine, string, string]
  >)('marks %o with a word, a glyph and the age', (line, words, feed) => {
    const { container } = render(<LiveStatusMarker status={status(line)} />)
    const marker = container.querySelector('[data-feed]') as HTMLElement
    expect(marker.getAttribute('data-feed')).toBe(feed)
    expect(marker).toHaveTextContent(words)
    expect(marker.querySelector('svg')).not.toBeNull()
    expect(marker.getAttribute('title')).toMatch(/\.$/)
  })

  it('keeps the age moving while it is shown', () => {
    render(<LiveStatusMarker status={status({ kind: 'polling', at: NOW, stale: false })} />)
    expect(screen.getByText('Delayed · updated 0s ago')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(4_000)
    })
    expect(screen.getByText('Delayed · updated 4s ago')).toBeInTheDocument()
  })

  it('tells a screen reader what a stale price means, in words that stay true', () => {
    const { container } = render(
      <LiveStatusMarker status={status({ kind: 'delayed', at: NOW - 25_000, stale: true })} />
    )
    expect(container).toHaveTextContent(
      'No price update since 09:59:35. The last price shown may be out of date.'
    )
  })

  it('shows nothing while prices are real-time, a first price is due, or history is fresh', () => {
    const { container, rerender } = render(<LiveStatusMarker status={status({ kind: 'live' })} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<LiveStatusMarker status={status({ kind: 'waiting' })} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<LiveStatusMarker status={status({ kind: 'delayed', at: NOW, stale: false })} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<LiveStatusMarker status={null} />)
    expect(container).toBeEmptyDOMElement()
    expect(vi.getTimerCount()).toBe(0)
  })
})
