/**
 * One gate for a REST read many things ask for: at most one read in the gap,
 * none while the page is hidden, and coming back owes exactly one read.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSpacedRead } from './useSpacedRead'

const GAP = 5_000
const AWAY = 15_000

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  vi.useRealTimers()
})

describe('useSpacedRead', () => {
  it('reads the first request at once, and every request inside the gap as one read at its end', async () => {
    const read = vi.fn()
    const { result } = renderHook(() => useSpacedRead(read, GAP))
    act(() => result.current())
    expect(read).toHaveBeenCalledTimes(1)

    for (let i = 0; i < 10; i++) {
      act(() => result.current())
      await pass(300)
    }
    expect(read).toHaveBeenCalledTimes(1)
    await pass(GAP)
    expect(read).toHaveBeenCalledTimes(2)
    await pass(60_000)
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('reads nothing while hidden, and once when seen again if anything asked', async () => {
    const read = vi.fn()
    const { result } = renderHook(() => useSpacedRead(read, GAP))
    act(() => setVisibility('hidden'))
    act(() => result.current())
    act(() => result.current())
    await pass(10_000)
    expect(read).not.toHaveBeenCalled()

    act(() => setVisibility('visible'))
    expect(read).toHaveBeenCalledTimes(1)
    await pass(GAP * 2)
    expect(read).toHaveBeenCalledTimes(1)
  })

  it('reads once on coming back after a long time away, not after a glance away', async () => {
    const read = vi.fn()
    renderHook(() => useSpacedRead(read, GAP, { awayMs: AWAY }))
    act(() => setVisibility('hidden'))
    await pass(5_000)
    act(() => setVisibility('visible'))
    expect(read).not.toHaveBeenCalled()

    act(() => setVisibility('hidden'))
    await pass(20_000)
    act(() => setVisibility('visible'))
    expect(read).toHaveBeenCalledTimes(1)
  })

  it('owes one read, not two, for a long time away during which something asked', async () => {
    const read = vi.fn()
    const { result } = renderHook(() => useSpacedRead(read, GAP, { awayMs: AWAY }))
    act(() => result.current())
    expect(read).toHaveBeenCalledTimes(1)

    act(() => setVisibility('hidden'))
    act(() => result.current())
    await pass(20_000)
    act(() => setVisibility('visible'))
    expect(read).toHaveBeenCalledTimes(2)
    await pass(GAP * 2)
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('reads nothing after unmounting, even with a read waiting at the end of the gap', async () => {
    const read = vi.fn()
    const { result, unmount } = renderHook(() => useSpacedRead(read, GAP, { awayMs: AWAY }))
    act(() => result.current())
    act(() => result.current())
    unmount()
    await pass(GAP * 2)
    act(() => setVisibility('hidden'))
    await pass(20_000)
    act(() => setVisibility('visible'))
    expect(read).toHaveBeenCalledTimes(1)
  })
})
