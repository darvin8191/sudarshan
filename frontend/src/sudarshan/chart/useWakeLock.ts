import { useCallback, useEffect, useRef, useState } from 'react'

/** The part of the Screen Wake Lock API this uses. */
interface Sentinel {
  release(): Promise<void>
  addEventListener(type: 'release', listener: () => void): void
}
interface WakeLockApi {
  request(type: 'screen'): Promise<Sentinel>
}

function wakeLockApi(): WakeLockApi | null {
  const api = (globalThis.navigator as (Navigator & { wakeLock?: WakeLockApi }) | undefined)
    ?.wakeLock
  return api && typeof api.request === 'function' ? api : null
}

/**
 * Keep the screen on while a chart is watched.
 *
 * The browser drops the lock whenever the page is hidden, so it is asked for
 * again each time the page comes back into view while the switch is on. Where
 * the browser has no wake lock, `supported` is false and the page shows no
 * switch at all; a refused request turns the switch back off rather than
 * claiming a lock nobody holds.
 */
export function useWakeLock(): { supported: boolean; on: boolean; setOn(on: boolean): void } {
  const [supported] = useState(() => wakeLockApi() !== null)
  const [on, setOnState] = useState(false)
  const sentinel = useRef<Sentinel | null>(null)
  const wanted = useRef(false)

  const acquire = useCallback(async () => {
    const api = wakeLockApi()
    if (!api || sentinel.current || document.visibilityState !== 'visible') return
    try {
      const lock = await api.request('screen')
      if (!wanted.current) {
        await lock.release()
        return
      }
      sentinel.current = lock
      lock.addEventListener('release', () => {
        if (sentinel.current === lock) sentinel.current = null
      })
    } catch {
      // Refused (battery saver, or no user gesture yet): say so by switching off.
      wanted.current = false
      setOnState(false)
    }
  }, [])

  const release = useCallback(() => {
    const lock = sentinel.current
    sentinel.current = null
    if (lock) void lock.release().catch(() => {})
  }, [])

  const setOn = useCallback(
    (next: boolean) => {
      wanted.current = next
      setOnState(next)
      if (next) void acquire()
      else release()
    },
    [acquire, release]
  )

  useEffect(() => {
    if (!supported) return
    const onVisibility = () => {
      if (wanted.current && document.visibilityState === 'visible') void acquire()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      wanted.current = false
      release()
    }
  }, [supported, acquire, release])

  return { supported, on, setOn }
}
