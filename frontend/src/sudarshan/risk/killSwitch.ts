import { useEffect, useSyncExternalStore } from 'react'
import type { Socket } from 'socket.io-client'
import type { KillSwitchState, RiskPipe, RiskStage, RiskState } from '@/api/risk'

/**
 * The kill switch as every page shows it (M20.7): the banner in both shells and the chip in
 * the account controls read this one app-wide copy.
 *
 * It is read once when a shell mounts, from the light route that makes no broker call
 * (/sudarshan/risk/state), and after that it moves with the server: the Socket.IO
 * `sudarshan_risk` event carries every change as it happens (turned on, its report, resumed,
 * from any surface), a reconnect reads it again because anything said meanwhile went to
 * nobody, and so does a page coming back to the front. Nothing polls.
 *
 * What cannot be read (a viewer's session, a lost connection, a server without the guard) is
 * shown as nothing rather than as an old state passed off as current. Plain `fetch` rather than
 * the query cache, because the shells render outside any one page.
 */

const STATE_URL = '/sudarshan/risk/state'

/** A page come back to the front reads again, at most this often. */
export const RESYNC_MS = 5_000

let current: RiskState | null = null
let reading: Promise<void> | null = null
let lastRead = 0
const listeners = new Set<() => void>()

function publish(next: RiskState | null): void {
  current = next
  for (const listener of [...listeners]) listener()
}

/** The switch as the server words it (`killswitch.public_state`), or null when it is not one. */
export function parseKillSwitch(value: unknown): KillSwitchState | null {
  if (typeof value !== 'object' || value === null) return null
  const state = value as KillSwitchState
  if (typeof state.known !== 'boolean') return null
  if (state.on !== null && typeof state.on !== 'boolean') return null
  return state
}

const STAGES: readonly RiskStage[] = ['off', 'sandbox', 'on']

function parseDaily(value: unknown): RiskState['daily'] {
  if (typeof value !== 'object' || value === null) return undefined
  const daily: NonNullable<RiskState['daily']> = {}
  for (const pipe of ['live', 'sandbox'] as RiskPipe[]) {
    const day = (value as Record<string, unknown>)[pipe]
    if (typeof day === 'object' && day !== null) {
      const { locked, unchecked, unchecked_left_s: left, paused } = day as Record<string, unknown>
      const shown = unchecked === true && typeof left === 'number' && left > 0
      daily[pipe] = {
        locked: locked === true,
        unchecked: shown,
        unchecked_left_s: shown ? left : null,
        unchecked_until: shown ? Date.now() + left * 1000 : null,
        paused: shown && paused === true,
      }
    }
  }
  return daily
}

function parseState(value: unknown): RiskState | null {
  if (typeof value !== 'object' || value === null) return null
  const { stage, kill_switch, daily } = value as {
    stage?: unknown
    kill_switch?: unknown
    daily?: unknown
  }
  const killSwitch = parseKillSwitch(kill_switch)
  if (!killSwitch || !STAGES.includes(stage as RiskStage)) return null
  return { stage: stage as RiskStage, kill_switch: killSwitch, daily: parseDaily(daily) }
}

/** Read the switch from the server; one read at a time. */
export function readKillSwitch(): Promise<void> {
  if (reading) return reading
  lastRead = Date.now()
  reading = (async () => {
    try {
      const response = await fetch(STATE_URL, {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) return // a viewer's session, or none: nothing to show
      const body = (await response.json()) as { data?: unknown }
      const state = parseState(body?.data)
      if (state) publish(state)
    } catch {
      // Not known now: the banner keeps what the server last said, and shows nothing at first.
    } finally {
      reading = null
    }
  })()
  return reading
}

/** The switch as the server just announced it, or as a page has just read it. */
export function noteKillSwitch(killSwitch: KillSwitchState, stage?: RiskStage): void {
  publish({
    stage: stage ?? current?.stage ?? 'off',
    kill_switch: killSwitch,
    daily: current?.daily,
  })
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function snapshot(): RiskState | null {
  return current
}

/** The stage and the switch, app-wide; null until the first read answers. */
export function useKillSwitch(): RiskState | null {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}

/** True while the switch is known to be on. */
export function killSwitchOn(state: RiskState | null): boolean {
  return state?.kill_switch.known === true && state.kill_switch.on === true
}

/** When the switch was turned on (ms since epoch) while it is on; null when off or not known. */
export function killSwitchOnSince(): number | null {
  if (!killSwitchOn(current)) return null
  const at = Date.parse(current?.kill_switch.since ?? '')
  return Number.isFinite(at) ? at : null
}

/** True when the server answered but could not read the switch: the gate refuses every new order. */
export function killSwitchUnread(state: RiskState | null): boolean {
  return state?.kill_switch.known === false
}

/** Whether turning the switch on cancels and exits in this book (Live, or the sandbox). */
export function actsOn(stage: RiskStage | undefined, sandbox: boolean): boolean {
  return stage === 'on' || (stage === 'sandbox' && sandbox)
}

/**
 * Where the switch reaches from the page the owner is on: `here` when it refuses this book's
 * orders, `sandbox` when the guard covers the sandbox only and the page trades Live (Live orders
 * are not checked), `none` when the guard is off on this server.
 */
export function reachOf(
  stage: RiskStage | undefined,
  sandbox: boolean
): 'here' | 'sandbox' | 'none' {
  if (actsOn(stage, sandbox)) return 'here'
  return stage === 'sandbox' ? 'sandbox' : 'none'
}

/** True while the daily loss limit has locked the book the page shows (M5.14). */
export function dayLocked(state: RiskState | null, sandbox: boolean): boolean {
  return state?.daily?.[sandbox ? 'sandbox' : 'live']?.locked === true
}

/**
 * Until when the daily loss limit is said to be not being checked in the book the page shows
 * (its positions could not be read), as a time in ms; null when it is being checked.
 */
export function dayUncheckedUntil(state: RiskState | null, sandbox: boolean): number | null {
  const day = state?.daily?.[sandbox ? 'sandbox' : 'live']
  if (!day?.unchecked || typeof day.unchecked_until !== 'number') return null
  return day.unchecked_until
}

/**
 * Whether the line above says new orders are paused (today's figure could not be read, so the
 * gate refuses them while exits go), rather than that orders go unchecked (the book could not be
 * read, so any order may be an exit).
 */
export function dayPaused(state: RiskState | null, sandbox: boolean): boolean {
  return state?.daily?.[sandbox ? 'sandbox' : 'live']?.paused === true
}

/**
 * Keeps the app-wide copy in step with the server. Rendered once per shell (the banner):
 * a read on mount, every `sudarshan_risk` event, a read on reconnect and on coming back.
 */
export function useKillSwitchSync(socket: Socket | null): void {
  useEffect(() => {
    void readKillSwitch()
  }, [])

  useEffect(() => {
    if (!socket) return
    const onRisk = (payload: unknown) => {
      const killSwitch = parseKillSwitch(payload)
      if (killSwitch) noteKillSwitch(killSwitch)
    }
    const onConnect = () => {
      void readKillSwitch()
    }
    socket.on('sudarshan_risk', onRisk)
    // The day locked, or the limit could not be checked: read the state again.
    socket.on('sudarshan_risk_day', onConnect)
    socket.on('connect', onConnect)
    return () => {
      socket.off('sudarshan_risk', onRisk)
      socket.off('sudarshan_risk_day', onConnect)
      socket.off('connect', onConnect)
    }
  }, [socket])

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && Date.now() - lastRead >= RESYNC_MS) {
        void readKillSwitch()
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])
}

/** Tests only: forget the copy. */
export function resetKillSwitchForTests(): void {
  current = null
  reading = null
  lastRead = 0
  for (const listener of [...listeners]) listener()
}
