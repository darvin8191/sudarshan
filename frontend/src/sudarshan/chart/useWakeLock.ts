import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

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

export interface WakeLockState {
  /** The browser can keep the screen on at all; where it cannot, the page shows no switch. */
  supported: boolean
  on: boolean
  /** The browser refused to keep the screen on, so the switch went off (M4.16, C12). */
  refused: boolean
  setOn(on: boolean): void
}

/**
 * Keep the screen on while a chart is watched.
 *
 * The browser drops the lock whenever the page is hidden, so it is asked for
 * again each time the page comes back into view while the switch is on, and
 * again at once if the phone drops it while the page is in view (battery
 * saver turned on, the battery running low). Where the browser has no wake
 * lock, `supported` is false and the page shows no switch at all. A refused
 * request turns the switch back off rather than claiming a lock nobody holds,
 * and says so through `refused` until the switch is next turned on.
 */
export function useWakeLock(): WakeLockState {
  const [supported] = useState(() => wakeLockApi() !== null)
  const [on, setOnState] = useState(false)
  const [refused, setRefused] = useState(false)
  const sentinel = useRef<Sentinel | null>(null)
  const wanted = useRef(false)
  /** A request is out: a second one meanwhile would leave the first lock held by nobody. */
  const asking = useRef(false)

  const acquire = useCallback(async () => {
    const api = wakeLockApi()
    if (!api || sentinel.current || asking.current || document.visibilityState !== 'visible') return
    asking.current = true
    try {
      const lock = await api.request('screen')
      if (!wanted.current) {
        await lock.release()
        return
      }
      sentinel.current = lock
      lock.addEventListener('release', () => {
        if (sentinel.current !== lock) return
        sentinel.current = null
        // Dropped by the phone while the chart is in view: ask again, and say so if refused.
        if (wanted.current && document.visibilityState === 'visible') void acquire()
      })
    } catch {
      // Refused (battery saver, or a phone that will not allow it): the switch goes off.
      wanted.current = false
      setOnState(false)
      setRefused(true)
    } finally {
      asking.current = false
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
      setRefused(false)
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

  // One value while nothing in it changes, so the chart screen it is handed to need not draw again.
  return useMemo(() => ({ supported, on, refused, setOn }), [supported, on, refused, setOn])
}
