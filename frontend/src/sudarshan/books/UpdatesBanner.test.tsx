/**
 * The line every page shows while the tab's connection is down (SIO1, rule 7): order events,
 * fills and alerts arrive on that connection, so while it is down, and while a refused connect
 * waits for its next try, the screens say "Order updates" with the app's own "Reconnecting" and
 * the age, in words and an icon. Nothing while it is up, nothing in the first seconds of a page's
 * first connect, and it is in both shells.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FIRST_CONNECT_GRACE_MS, STALE_AFTER_MS, UpdatesLine } from './UpdatesBanner'

type Handler = () => void

class FakeSocket {
  connected = false
  private handlers = new Map<string, Set<Handler>>()
  on(event: string, handler: Handler) {
    this.handlers.set(event, (this.handlers.get(event) ?? new Set()).add(handler))
    return this
  }
  off(event: string, handler: Handler) {
    this.handlers.get(event)?.delete(handler)
    return this
  }
  fire(event: string) {
    for (const handler of [...(this.handlers.get(event) ?? [])]) handler()
  }
}

const line = () => screen.queryByTestId('updates-banner')

afterEach(() => {
  vi.useRealTimers()
})

describe('the order updates line', () => {
  it('says Reconnecting with the age while the connection is down, and goes once it is back', () => {
    vi.useFakeTimers()
    const socket = new FakeSocket()
    socket.connected = true
    render(<UpdatesLine socket={socket as never} />)
    expect(line()).toBeNull()

    act(() => {
      socket.connected = false
      socket.fire('disconnect')
    })
    expect(line()?.textContent).toContain('Order updates')
    expect(line()?.textContent).toContain('Reconnecting')

    act(() => {
      vi.advanceTimersByTime(12_000) // a refused connect waiting for its next try, say
    })
    expect(line()?.textContent).toContain('Reconnecting · updated 12s ago')
    expect(line()?.querySelector('svg')).not.toBeNull() // an icon beside the words

    act(() => {
      socket.connected = true
      socket.fire('connect')
    })
    expect(line()).toBeNull()
  })

  it('takes the attention colour when the connection has been down a while', () => {
    vi.useFakeTimers()
    const socket = new FakeSocket()
    socket.connected = true
    render(<UpdatesLine socket={socket as never} />)
    act(() => {
      socket.connected = false
      socket.fire('disconnect')
    })
    expect(line()?.innerHTML).not.toContain('warning-text')
    act(() => {
      vi.advanceTimersByTime(STALE_AFTER_MS + 1_000)
    })
    expect(line()?.innerHTML).toContain('warning-text')
  })

  it('gives a first connect a few seconds before it says Connecting', () => {
    vi.useFakeTimers()
    const socket = new FakeSocket() // never up yet
    render(<UpdatesLine socket={socket as never} />)
    expect(line()).toBeNull()
    act(() => {
      vi.advanceTimersByTime(FIRST_CONNECT_GRACE_MS + 1_000)
    })
    expect(line()?.textContent).toContain('Connecting')
    expect(line()?.textContent).not.toContain('Reconnecting')
  })

  it('shows nothing where there is no connection to watch', () => {
    render(<UpdatesLine socket={null} />)
    expect(line()).toBeNull()
  })

  it('is in both shells, beside the Analyze and kill switch banners', () => {
    const shells = join(process.cwd(), 'src', 'components', 'layout')
    for (const shell of ['Layout.tsx', 'FullWidthLayout.tsx']) {
      expect(readFileSync(join(shells, shell), 'utf8')).toContain('<UpdatesBanner />')
    }
  })

  it('has no dark styles', () => {
    const source = readFileSync(
      join(process.cwd(), 'src', 'sudarshan', 'books', 'UpdatesBanner.tsx'),
      'utf8'
    )
    expect(source).not.toMatch(/\bdark:/)
  })
})
