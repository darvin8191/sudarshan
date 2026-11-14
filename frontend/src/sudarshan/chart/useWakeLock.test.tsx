import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useWakeLock } from './useWakeLock'

class FakeSentinel {
  released = false
  private readonly listeners: Array<() => void> = []
  async release() {
    this.released = true
    for (const listener of this.listeners) listener()
  }
  addEventListener(_type: 'release', listener: () => void) {
    this.listeners.push(listener)
  }
}

function installWakeLock(request: () => Promise<FakeSentinel>) {
  // A request the phone answers: each one is a new lock.
  Object.defineProperty(navigator, 'wakeLock', {
    configurable: true,
    value: { request: vi.fn(request) },
  })
  return (navigator as unknown as { wakeLock: { request: ReturnType<typeof vi.fn> } }).wakeLock
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: state })
  document.dispatchEvent(new Event('visibilitychange'))
}

afterEach(() => {
  Reflect.deleteProperty(navigator, 'wakeLock')
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
})

describe('keep the screen on', () => {
  it('is not offered where the browser cannot do it', () => {
    const { result } = renderHook(() => useWakeLock())
    expect(result.current.supported).toBe(false)
  })

  it('holds the screen on while the switch is on, and lets it go when switched off', async () => {
    const sentinels: FakeSentinel[] = []
    const api = installWakeLock(async () => {
      const lock = new FakeSentinel()
      sentinels.push(lock)
      return lock
    })
    const { result } = renderHook(() => useWakeLock())
    expect(result.current.supported).toBe(true)
    await act(async () => result.current.setOn(true))
    expect(result.current.on).toBe(true)
    expect(api.request).toHaveBeenCalledWith('screen')
    await act(async () => result.current.setOn(false))
    expect(result.current.on).toBe(false)
    expect(sentinels[0].released).toBe(true)
  })

  it('asks again when the page comes back into view, since the browser dropped it', async () => {
    const sentinels: FakeSentinel[] = []
    const api = installWakeLock(async () => {
      const lock = new FakeSentinel()
      sentinels.push(lock)
      return lock
    })
    const { result } = renderHook(() => useWakeLock())
    await act(async () => result.current.setOn(true))
    // The browser releases the lock when the page is hidden.
    await act(async () => {
      setVisibility('hidden')
      await sentinels[0].release()
    })
    await act(async () => setVisibility('visible'))
    expect(api.request).toHaveBeenCalledTimes(2)
    expect(result.current.on).toBe(true)
  })

  it('switches itself off when the browser refuses, rather than claim a lock nobody holds', async () => {
    installWakeLock(() => Promise.reject(new Error('battery saver is on')))
    const { result } = renderHook(() => useWakeLock())
    expect(result.current.refused).toBe(false)
    await act(async () => result.current.setOn(true))
    expect(result.current.on).toBe(false)
    expect(result.current.refused).toBe(true)
  })

  it('says it was refused until the switch is next turned on, and holds the lock when that is granted (C12)', async () => {
    let allow = false
    installWakeLock(async () => {
      if (!allow) throw new Error('battery saver is on')
      return new FakeSentinel()
    })
    const { result } = renderHook(() => useWakeLock())
    await act(async () => result.current.setOn(true))
    expect(result.current.refused).toBe(true)
    allow = true
    await act(async () => result.current.setOn(true))
    expect(result.current.refused).toBe(false)
    expect(result.current.on).toBe(true)
  })

  it('asks again at once when the phone drops the lock with the chart in view (C12)', async () => {
    const sentinels: FakeSentinel[] = []
    const api = installWakeLock(async () => {
      const lock = new FakeSentinel()
      sentinels.push(lock)
      return lock
    })
    const { result } = renderHook(() => useWakeLock())
    await act(async () => result.current.setOn(true))
    await act(async () => sentinels[0].release())
    expect(api.request).toHaveBeenCalledTimes(2)
    expect(result.current.on).toBe(true)
    expect(result.current.refused).toBe(false)
  })

  it('switches off and says so when the phone drops the lock in view and will not give it back (C12)', async () => {
    const sentinels: FakeSentinel[] = []
    installWakeLock(async () => {
      if (sentinels.length > 0) throw new Error('battery saver turned on')
      const lock = new FakeSentinel()
      sentinels.push(lock)
      return lock
    })
    const { result } = renderHook(() => useWakeLock())
    await act(async () => result.current.setOn(true))
    expect(result.current.on).toBe(true)
    await act(async () => sentinels[0].release())
    expect(result.current.on).toBe(false)
    expect(result.current.refused).toBe(true)
  })

  it('asks once at a time, so no lock is left held by nobody (C12)', async () => {
    let answer: ((lock: FakeSentinel) => void) | null = null
    const api = installWakeLock(
      () =>
        new Promise<FakeSentinel>((resolve) => {
          answer = resolve
        })
    )
    const { result } = renderHook(() => useWakeLock())
    await act(async () => result.current.setOn(true))
    // The page comes back into view while the first request is still out.
    await act(async () => setVisibility('visible'))
    expect(api.request).toHaveBeenCalledOnce()
    const lock = new FakeSentinel()
    await act(async () => answer?.(lock))
    await act(async () => result.current.setOn(false))
    expect(lock.released).toBe(true)
  })

  it('lets the screen go when the chart closes', async () => {
    const sentinels: FakeSentinel[] = []
    installWakeLock(async () => {
      const lock = new FakeSentinel()
      sentinels.push(lock)
      return lock
    })
    const { result, unmount } = renderHook(() => useWakeLock())
    await act(async () => result.current.setOn(true))
    unmount()
    expect(sentinels[0].released).toBe(true)
  })
})
