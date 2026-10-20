import { useSyncExternalStore } from 'react'

/**
 * The soft sound that goes with an order alert, and its per-device mute.
 *
 * Drawn with the Web Audio API rather than an audio file: a short sine note
 * with a quick fade in and out, quiet enough not to startle, different enough
 * for a rejection to be told from a fill without looking. Chrome lets a page
 * make a sound only after the person has touched it, so the audio context is
 * started by the first tap or key press anywhere on the page; until then an
 * alert is shown without its sound.
 *
 * The mute is a preference of this device, kept in this browser like the
 * colour-blind switch (src/sudarshan/display.ts), and it follows a change made
 * in another tab.
 */
const STORAGE_KEY = 'sudarshan-order-sound'

export type ChimeKind = 'filled' | 'partial' | 'rejected' | 'cancelled'

/** Two notes per kind, in hertz: rising for a fill, falling for a rejection. */
const NOTES: Record<ChimeKind, [number, number]> = {
  filled: [660, 880],
  partial: [660, 660],
  rejected: [494, 370],
  cancelled: [587, 523],
}

/** A burst of alerts (a basket, both legs of a spread) sounds once. */
const QUIET_MS = 700

const listeners = new Set<() => void>()

function stored(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'off'
  } catch {
    return false
  }
}

let muted = stored()

function publish(): void {
  for (const listener of listeners) listener()
}

export function setOrderSoundMuted(on: boolean): void {
  muted = on
  try {
    if (on) localStorage.setItem(STORAGE_KEY, 'off')
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Storage refused: the choice holds until the page is closed.
  }
  publish()
}

export function orderSoundMuted(): boolean {
  return muted
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Whether order sounds are muted on this device, re-rendering when that changes. */
export function useOrderSoundMuted(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => muted,
    () => false
  )
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY && event.key !== null) return
    muted = stored()
    publish()
  })
}

type AudioContextClass = typeof AudioContext

let context: AudioContext | null = null
let lastPlayedAt = 0
let unlockInstalled = false

function audioContextClass(): AudioContextClass | null {
  if (typeof window === 'undefined') return null
  const scope = window as typeof window & { webkitAudioContext?: AudioContextClass }
  return scope.AudioContext ?? scope.webkitAudioContext ?? null
}

function startContext(): void {
  const Context = audioContextClass()
  if (!Context) return
  try {
    context ??= new Context()
    if (context.state === 'suspended') void context.resume().catch(() => {})
  } catch {
    context = null
  }
}

/**
 * Start the audio context on the first tap or key press, as Chrome requires.
 * Safe to call more than once.
 */
export function unlockSoundOnFirstTap(): void {
  if (unlockInstalled || typeof document === 'undefined') return
  unlockInstalled = true
  // The events a browser counts as the person activating the page.
  const events = ['pointerdown', 'pointerup', 'touchend', 'click', 'keydown'] as const
  const unlock = () => {
    startContext()
    if (context?.state === 'running') {
      for (const event of events) document.removeEventListener(event, unlock, true)
    }
  }
  for (const event of events) document.addEventListener(event, unlock, true)
}

/** Play the alert sound for an order, unless muted, not yet unlocked, or just played. */
export function playChime(kind: ChimeKind, now = Date.now()): boolean {
  if (muted || !context || context.state !== 'running') return false
  if (now - lastPlayedAt < QUIET_MS) return false
  lastPlayedAt = now
  try {
    const start = context.currentTime + 0.01
    const [first, second] = NOTES[kind]
    const gain = context.createGain()
    gain.gain.setValueAtTime(0.0001, start)
    gain.gain.exponentialRampToValueAtTime(0.07, start + 0.02)
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.34)
    gain.connect(context.destination)
    const note = context.createOscillator()
    note.type = 'sine'
    note.frequency.setValueAtTime(first, start)
    note.frequency.setValueAtTime(second, start + 0.12)
    note.connect(gain)
    note.start(start)
    note.stop(start + 0.36)
    note.onended = () => {
      note.disconnect()
      gain.disconnect()
    }
    return true
  } catch {
    return false
  }
}

/** Tests only: forget the audio context and the last sound. */
export function resetChimeForTests(): void {
  context = null
  lastPlayedAt = 0
  unlockInstalled = false
}
