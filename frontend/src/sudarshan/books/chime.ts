import { useSyncExternalStore } from 'react'

/**
 * The soft sound that goes with an order alert, and its per-device mute.
 *
 * Drawn with the Web Audio API rather than an audio file: a short sine note
 * with a quick fade in and out, quiet enough not to startle, different enough
 * for a rejection to be told from a fill without looking. Chrome lets a page
 * make a sound only after the person has touched it, so the audio context is
 * started by the first tap or key press anywhere on the page; until then an
 * alert is shown without its sound. Chrome can suspend the context again
 * later (a phone that slept, a tab long in the background), so the next tap
 * starts it again.
 *
 * One sound per moment: an order placed and filled at once raises the app's
 * own placement sound and this one together, so whichever asks first plays
 * and the other stays quiet (claimOrderSound).
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
let lastOrderSoundAt = Number.NEGATIVE_INFINITY
let tapWatch = false

/** The events a browser counts as the person activating the page. */
const TAP_EVENTS = ['pointerdown', 'pointerup', 'touchend', 'click', 'keydown'] as const

function audioContextClass(): AudioContextClass | null {
  if (typeof window === 'undefined') return null
  const scope = window as typeof window & { webkitAudioContext?: AudioContextClass }
  return scope.AudioContext ?? scope.webkitAudioContext ?? null
}

function stopWatchingTaps(): void {
  if (!tapWatch || typeof document === 'undefined') return
  tapWatch = false
  for (const event of TAP_EVENTS) document.removeEventListener(event, onTap, true)
}

function watchTaps(): void {
  if (tapWatch || typeof document === 'undefined') return
  tapWatch = true
  for (const event of TAP_EVENTS) document.addEventListener(event, onTap, true)
}

function onTap(): void {
  const Context = audioContextClass()
  if (!Context) {
    stopWatchingTaps()
    return
  }
  try {
    if (!context) {
      context = new Context()
      // Suspended again later: start it on the next tap.
      context.onstatechange = () => {
        if (context && context.state !== 'running') watchTaps()
      }
    }
    if (context.state === 'running') {
      stopWatchingTaps()
      return
    }
    void context
      .resume()
      .then(() => {
        if (context?.state === 'running') stopWatchingTaps()
      })
      .catch(() => {})
  } catch {
    context = null
  }
}

/**
 * Start the audio context on the first tap or key press, as Chrome requires,
 * and again whenever it has been suspended since. Safe to call more than once.
 */
export function unlockSoundOnFirstTap(): void {
  if (context?.state === 'running') return
  watchTaps()
}

/**
 * Claim the moment for an order sound. True if no order sound has played in
 * the last `QUIET_MS`, and then nothing else plays until it has passed. The
 * app's own placement sound asks here too (hooks/useSocket.ts).
 */
export function claimOrderSound(now = Date.now()): boolean {
  if (now - lastOrderSoundAt < QUIET_MS) return false
  lastOrderSoundAt = now
  return true
}

/** Play the alert sound for an order, unless muted, not yet unlocked, or just played. */
export function playChime(kind: ChimeKind, now = Date.now()): boolean {
  if (muted) return false
  if (!context || context.state !== 'running') {
    // Not started, or suspended since: the next tap starts it.
    watchTaps()
    return false
  }
  if (!claimOrderSound(now)) return false
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

/** Tests only: forget the audio context, the last sound and the tap watch. */
export function resetChimeForTests(): void {
  stopWatchingTaps()
  context = null
  lastOrderSoundAt = Number.NEGATIVE_INFINITY
}
