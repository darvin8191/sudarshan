/**
 * The order alert sound (M6.8): drawn with the Web Audio API, silent until a
 * tap starts the audio context (Chrome's rule), again after Chrome has
 * suspended it, played once for a burst, and muted per device with the choice
 * kept in this browser and read back when the app loads.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  claimOrderSound,
  orderSoundMuted,
  playChime,
  resetChimeForTests,
  setOrderSoundMuted,
  unlockSoundOnFirstTap,
} from './chime'

class FakeParam {
  setValueAtTime() {}
  exponentialRampToValueAtTime() {}
}

class FakeNode {
  gain = new FakeParam()
  frequency = new FakeParam()
  type = 'sine'
  onended: (() => void) | null = null
  connect() {
    return this
  }
  disconnect() {}
  start() {}
  stop() {}
}

const created: FakeContext[] = []

/** How a new audio context starts: Chrome starts one suspended until the page is touched. */
let startState: 'suspended' | 'running' = 'running'

class FakeContext {
  state: 'suspended' | 'running' = startState
  currentTime = 0
  destination = {}
  oscillators = 0
  resumes = 0
  onstatechange: (() => void) | null = null
  constructor() {
    created.push(this)
  }
  resume() {
    this.resumes += 1
    this.state = 'running'
    this.onstatechange?.()
    return Promise.resolve()
  }
  /** Chrome suspends it again: a phone that slept, a tab long in the background. */
  suspendByBrowser() {
    this.state = 'suspended'
    this.onstatechange?.()
  }
  createGain() {
    return new FakeNode()
  }
  createOscillator() {
    this.oscillators += 1
    return new FakeNode()
  }
}

beforeEach(() => {
  created.length = 0
  startState = 'running'
  resetChimeForTests()
  setOrderSoundMuted(false)
  vi.stubGlobal('AudioContext', FakeContext)
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  setOrderSoundMuted(false)
  localStorage.clear()
})

describe('order alert sound', () => {
  it('makes no sound before the first tap, then one note per alert', () => {
    unlockSoundOnFirstTap()
    expect(playChime('filled', 1_000)).toBe(false)
    expect(created).toHaveLength(0)

    document.dispatchEvent(new Event('pointerdown'))
    expect(created).toHaveLength(1)
    expect(playChime('filled', 2_000)).toBe(true)
    expect(created[0].oscillators).toBe(1)
  })

  it('sounds a burst of alerts once', () => {
    unlockSoundOnFirstTap()
    document.dispatchEvent(new Event('pointerdown'))
    expect(playChime('filled', 10_000)).toBe(true)
    expect(playChime('filled', 10_200)).toBe(false)
    expect(playChime('rejected', 11_000)).toBe(true)
  })

  it('stays silent when muted on this device, and remembers it', () => {
    unlockSoundOnFirstTap()
    document.dispatchEvent(new Event('pointerdown'))
    setOrderSoundMuted(true)

    expect(orderSoundMuted()).toBe(true)
    expect(localStorage.getItem('sudarshan-order-sound')).toBe('off')
    expect(playChime('filled', 20_000)).toBe(false)

    setOrderSoundMuted(false)
    expect(localStorage.getItem('sudarshan-order-sound')).toBeNull()
    expect(playChime('filled', 21_000)).toBe(true)
  })

  it('stays silent while the audio is suspended, until a tap starts it', async () => {
    startState = 'suspended'
    unlockSoundOnFirstTap()
    document.dispatchEvent(new Event('pointerdown'))
    // The context exists but Chrome has not let it run yet.
    expect(created).toHaveLength(1)
    await Promise.resolve()
    expect(created[0].state).toBe('running')
    expect(playChime('filled', 40_000)).toBe(true)
  })

  it('needs a tap again after Chrome suspends the audio later', async () => {
    unlockSoundOnFirstTap()
    document.dispatchEvent(new Event('pointerdown'))
    expect(playChime('filled', 50_000)).toBe(true)

    created[0].suspendByBrowser()
    expect(playChime('filled', 51_000)).toBe(false)
    document.dispatchEvent(new Event('touchend'))
    await Promise.resolve()
    expect(created[0].resumes).toBe(1)
    expect(playChime('filled', 52_000)).toBe(true)
  })

  it('shares one moment with the placement sound: whichever asks first plays', () => {
    unlockSoundOnFirstTap()
    document.dispatchEvent(new Event('pointerdown'))
    expect(claimOrderSound(60_000)).toBe(true)
    expect(playChime('filled', 60_300)).toBe(false)
    expect(playChime('filled', 61_000)).toBe(true)
    expect(claimOrderSound(61_200)).toBe(false)
  })

  it('reads the mute back when the app loads', async () => {
    localStorage.setItem('sudarshan-order-sound', 'off')
    vi.resetModules()
    const fresh = await import('./chime')
    expect(fresh.orderSoundMuted()).toBe(true)
    localStorage.removeItem('sudarshan-order-sound')
  })

  it('does nothing in a browser without Web Audio', () => {
    vi.stubGlobal('AudioContext', undefined)
    unlockSoundOnFirstTap()
    document.dispatchEvent(new Event('pointerdown'))
    expect(playChime('filled', 30_000)).toBe(false)
  })
})
