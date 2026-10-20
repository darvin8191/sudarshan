/**
 * The clock live screens age their figures by (M8.2): a step a second while
 * the page is in view, none while it is hidden, and one at once when it comes
 * back, so the first frame after the phone wakes shows the true age.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useNow } from './hooks'

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  act(() => {
    document.dispatchEvent(new Event('visibilitychange'))
  })
}

beforeEach(() => {
  vi.useFakeTimers({
    toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'],
  })
  setVisibility('visible')
})

afterEach(() => {
  setVisibility('visible')
  vi.useRealTimers()
})

describe('useNow', () => {
  it('steps every second while the page is in view', () => {
    const { result } = renderHook(() => useNow())
    const start = result.current
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(result.current - start).toBe(3000)
  })

  it('holds still while the page is hidden, and catches up the moment it is back', () => {
    const { result } = renderHook(() => useNow())
    const start = result.current

    setVisibility('hidden')
    act(() => {
      vi.advanceTimersByTime(30_500)
    })
    expect(result.current).toBe(start)

    // Back in view between two steps: the clock moves now, not at the next step.
    setVisibility('visible')
    expect(result.current - start).toBe(30_500)
  })

  it('lets go of its timer and its listener when it unmounts', () => {
    const remove = vi.spyOn(document, 'removeEventListener')
    const { unmount } = renderHook(() => useNow())
    unmount()
    expect(remove).toHaveBeenCalledWith('visibilitychange', expect.any(Function))
    expect(vi.getTimerCount()).toBe(0)
    remove.mockRestore()
  })
})
