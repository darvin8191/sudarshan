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
    await act(async () => result.current.setOn(true))
    expect(result.current.on).toBe(false)
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
