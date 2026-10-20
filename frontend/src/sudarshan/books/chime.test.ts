/**
 * The order alert sound (M6.8): drawn with the Web Audio API, silent until the
 * first tap starts the audio context (Chrome's rule), played once for a burst,
 * and muted per device with the choice kept in this browser.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
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

class FakeContext {
  state: 'suspended' | 'running' = 'running'
  currentTime = 0
  destination = {}
  oscillators = 0
  constructor() {
    created.push(this)
  }
  resume() {
    this.state = 'running'
    return Promise.resolve()
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

  it('does nothing in a browser without Web Audio', () => {
    vi.stubGlobal('AudioContext', undefined)
    unlockSoundOnFirstTap()
    document.dispatchEvent(new Event('pointerdown'))
    expect(playChime('filled', 30_000)).toBe(false)
  })
})
