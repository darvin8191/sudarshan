/**
 * SUDARSHAN (SCR-F): what a reader of the market data context renders for.
 * The provider's page-visibility clock used to hand every reader a new value
 * each second, so every page under it drew itself again each second.
 */
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useIsPageVisible, usePageVisibility } from '@/hooks/usePageVisibility'
import type { MarketDataManager } from '@/lib/MarketDataManager'
import { MarketDataProvider, useMarketDataContext } from './MarketDataContext'

type State = ReturnType<MarketDataManager['getState']>

/** The manager as the provider uses it: its state, and the pause it is asked for. */
function fakeManager() {
  let state = {
    connectionState: 'authenticated',
    isConnected: true,
    isAuthenticated: true,
    isPaused: false,
    isFallbackMode: false,
    connectionEpoch: 1,
    error: null,
  } as unknown as State
  const listeners = new Set<(state: State) => void>()
  const set = (patch: Partial<State>) => {
    state = { ...state, ...patch }
    for (const listener of listeners) listener(state)
  }
  const manager = {
    getState: () => state,
    addStateListener: (listener: (state: State) => void) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    pauseConnection: vi.fn(() => set({ isPaused: true, isConnected: false })),
    resumeConnection: vi.fn(() => set({ isPaused: false, isConnected: true })),
  }
  return { manager: manager as unknown as MarketDataManager, set, calls: manager }
}

function setVisibility(value: DocumentVisibilityState, tell = true) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value })
  if (tell) document.dispatchEvent(new Event('visibilitychange'))
}

/** Time passing a second at a time, each its own task as in a browser, so each may render. */
async function seconds(n: number) {
  for (let left = n * 1000; left > 0; left -= 1000)
    await act(() => vi.advanceTimersByTimeAsync(Math.min(left, 1000)))
}
/** A visibility change, taken up by the page (the hook commits it in a later task). */
async function page(value: DocumentVisibilityState) {
  setVisibility(value)
  await act(() => vi.advanceTimersByTimeAsync(0))
}

beforeEach(() => {
  vi.useFakeTimers()
  setVisibility('visible', false)
})
afterEach(() => {
  Reflect.deleteProperty(document, 'visibilityState')
  vi.useRealTimers()
})

describe('a reader of the market data context', () => {
  it('renders once in a quiet minute, and again only when the connection changes', async () => {
    const fake = fakeManager()
    let renders = 0
    const values = new Set<unknown>()
    function Reader() {
      values.add(useMarketDataContext())
      renders++
      return null
    }
    render(
      <MarketDataProvider manager={fake.manager}>
        <Reader />
      </MarketDataProvider>
    )
    expect(renders).toBe(1)
    // A minute with nothing changing: sixty renders, and sixty values, before.
    await seconds(60)
    expect(renders).toBe(1)
    expect(values.size).toBe(1)
    // The stream reconnects: that is something a reader uses.
    act(() => fake.set({ connectionEpoch: 2 }))
    expect(renders).toBe(2)
    await seconds(60)
    expect(renders).toBe(2)
  })

  it('still pauses the stream five seconds after the page is hidden, and resumes it when shown', async () => {
    const fake = fakeManager()
    let renders = 0
    function Reader() {
      useMarketDataContext()
      renders++
      return null
    }
    render(
      <MarketDataProvider manager={fake.manager}>
        <Reader />
      </MarketDataProvider>
    )
    await page('hidden')
    await seconds(4)
    expect(fake.calls.pauseConnection).not.toHaveBeenCalled()
    // Hidden, and nothing a reader uses has changed yet.
    expect(renders).toBe(1)
    await seconds(1)
    expect(fake.calls.pauseConnection).toHaveBeenCalledOnce()
    const paused = renders
    await seconds(30)
    expect(renders).toBe(paused)
    await page('visible')
    expect(fake.calls.resumeConnection).toHaveBeenCalledOnce()
  })
})

describe('the page visibility hook', () => {
  function counted(hook: () => unknown) {
    let renders = 0
    function Reader() {
      hook()
      renders++
      return null
    }
    render(<Reader />)
    return () => renders
  }

  it('renders a reader of visibility alone only when the page is hidden or shown', async () => {
    const renders = counted(useIsPageVisible)
    await seconds(30)
    expect(renders()).toBe(1)
    await page('hidden')
    const hidden = renders()
    expect(hidden).toBeGreaterThan(1)
    await seconds(30)
    expect(renders()).toBe(hidden)
  })

  it('keeps the once-a-second counts for a reader that asks for them', async () => {
    let since = 0
    const renders = counted(() => {
      since = usePageVisibility().timeSinceVisible
    })
    await seconds(10)
    expect(renders()).toBeGreaterThanOrEqual(10)
    expect(since).toBeGreaterThanOrEqual(9_000)
  })
})
