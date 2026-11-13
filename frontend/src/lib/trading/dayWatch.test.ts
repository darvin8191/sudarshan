import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { istDay, msToNextIstMidnight, watchIstDay } from './dayWatch'

/** Epoch ms of a time in India on a day of December 2026. */
const at = (day: number, h: number, mi: number, s = 0) =>
  Date.UTC(2026, 11, day, h, mi, s) - 19_800_000

let visibility: 'visible' | 'hidden' = 'visible'
function setVisibility(state: 'visible' | 'hidden') {
  visibility = state
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('the date change in India (review N3)', () => {
  beforeEach(() => {
    // The clock moves only when the test moves it: the moment just before the timer is testable.
    vi.useFakeTimers({
      shouldAdvanceTime: false,
      toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'],
    })
    visibility = 'visible'
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => visibility,
    })
  })
  afterEach(() => {
    vi.useRealTimers()
    Reflect.deleteProperty(document, 'visibilityState')
  })

  it('names the date in India and the wait to its midnight', () => {
    expect(istDay(at(7, 23, 59, 59))).toBe('2026-12-07')
    expect(istDay(at(8, 0, 0))).toBe('2026-12-08')
    expect(msToNextIstMidnight(at(7, 23, 59, 59))).toBe(1_000)
    expect(msToNextIstMidnight(at(8, 0, 0))).toBe(86_400_000)
  })

  it('calls back once at the midnight, with one timer and no poll', async () => {
    vi.setSystemTime(at(7, 23, 0))
    const heard = vi.fn()
    const stop = watchIstDay(heard)
    expect(vi.getTimerCount()).toBe(1)
    await vi.advanceTimersByTimeAsync(3_600_000) // 00:00:00 exactly
    expect(heard).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(249)
    expect(heard).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(heard).toHaveBeenCalledTimes(1)
    expect(heard).toHaveBeenCalledWith('2026-12-08')
    expect(vi.getTimerCount()).toBe(1) // the next midnight
    await vi.advanceTimersByTimeAsync(23 * 3_600_000)
    expect(heard).toHaveBeenCalledTimes(1)
    stop()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('catches a phone that slept past midnight when it is visible or focused again', async () => {
    vi.setSystemTime(at(7, 22, 0))
    const heard = vi.fn()
    const stop = watchIstDay(heard)
    vi.setSystemTime(at(8, 7, 0)) // asleep: the timer did not run
    setVisibility('hidden')
    expect(heard).not.toHaveBeenCalled()
    setVisibility('visible')
    expect(heard).toHaveBeenCalledTimes(1)
    window.dispatchEvent(new Event('focus'))
    setVisibility('visible')
    expect(heard).toHaveBeenCalledTimes(1) // the date it belongs to is today's now
    expect(vi.getTimerCount()).toBe(1)
    stop()
    window.dispatchEvent(new Event('focus'))
    expect(heard).toHaveBeenCalledTimes(1)
  })

  it('a wake-up on the same day asks nothing and keeps the one timer', () => {
    vi.setSystemTime(at(7, 10, 0))
    const heard = vi.fn()
    const stop = watchIstDay(heard)
    window.dispatchEvent(new Event('focus'))
    setVisibility('visible')
    expect(heard).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(1)
    stop()
  })
})
