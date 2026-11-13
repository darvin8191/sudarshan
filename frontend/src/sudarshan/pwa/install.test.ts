// SUDARSHAN P6 D8: the install prompt is held until the member taps, used once, and offered only
// where Chrome gave one.
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installState, promptInstall, resetInstallForTests, useInstallState } from './install'

function offer(outcome: 'accepted' | 'dismissed' = 'accepted') {
  const event = new Event('beforeinstallprompt', { cancelable: true }) as Event & {
    prompt: ReturnType<typeof vi.fn>
    userChoice: Promise<{ outcome: string }>
  }
  event.prompt = vi.fn(async () => undefined)
  event.userChoice = Promise.resolve({ outcome })
  window.dispatchEvent(event)
  return event
}

beforeEach(() => {
  resetInstallForTests()
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
})
afterEach(() => vi.unstubAllGlobals())

describe('the install prompt', () => {
  it('is unavailable until Chrome offers it', () => {
    expect(installState()).toBe('unavailable')
  })

  it("holds Chrome's own bar back and shows nothing until a tap", () => {
    const event = offer()
    expect(event.defaultPrevented).toBe(true)
    expect(event.prompt).not.toHaveBeenCalled()
    expect(installState()).toBe('available')
  })

  it('shows the question on a tap, once, and remembers an accepted answer', async () => {
    const event = offer('accepted')
    expect(await promptInstall()).toBe('accepted')
    expect(event.prompt).toHaveBeenCalledTimes(1)
    expect(installState()).toBe('installed')
    expect(await promptInstall()).toBe('unavailable')
  })

  it('is used up by a dismissal: the event cannot be shown twice', async () => {
    const event = offer('dismissed')
    expect(await promptInstall()).toBe('dismissed')
    expect(installState()).toBe('unavailable')
    expect(event.prompt).toHaveBeenCalledTimes(1)
  })

  it('says installed once the app was added, and when it runs as an app', () => {
    offer()
    window.dispatchEvent(new Event('appinstalled'))
    expect(installState()).toBe('installed')
    resetInstallForTests()
    vi.stubGlobal('matchMedia', () => ({ matches: true }))
    expect(installState()).toBe('installed')
  })

  it('tells a component when Chrome offers it', () => {
    const { result } = renderHook(() => useInstallState())
    expect(result.current).toBe('unavailable')
    act(() => void offer())
    expect(result.current).toBe('available')
  })
})
