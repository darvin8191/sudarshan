// SUDARSHAN P6 D8 (M10.6): Web Push for a family phone. Three jobs, all through the family
// viewer API and the family worker (scope /family/); nothing here touches an owner path or the
// owner's keys. The server's public key comes from the viewer API, never from this bundle.
//
//  - `enablePush`: on a tap only, ask Chrome's permission, subscribe with the service's key and
//    tell the service. The permission prompt is never shown without a tap.
//  - `resyncPush`: on every open of the app, when permission is already granted, post the
//    subscription the browser holds NOW. Chrome does not always say when a subscription changes
//    (`pushsubscriptionchange` is not reliable), so the service is told again each time rather
//    than trusted to have kept the last one. A subscription the browser lost is made again, but
//    only on a phone where the member had turned notifications on.
//  - `disablePush`: tell the service, then drop the browser's subscription.
//
// Endpoints and keys are never logged or kept; the only thing stored on the phone is one flag,
// `on` or absent, which says the member turned notifications on here.
import { useEffect } from 'react'
import { FamilyError, familyApi } from '@/sudarshan/family/api'
import { registerFamilyWorker } from './register'

export const BLOCKED_WORDS =
  "Notifications are blocked for this site in Chrome. Open Chrome's site settings to allow them."
export const UNSUPPORTED_WORDS = 'This phone cannot show notifications from this app.'

const FLAG = 'sd-family-push'
const LAST = 'sd-family-push-endpoint'

export type PushStatus =
  | 'on'
  | 'off'
  /** Chrome's site settings say no: only the member can change that. */
  | 'blocked'
  | 'unsupported'

function read(name: string): string | null {
  try {
    return window.localStorage.getItem(name)
  } catch {
    return null
  }
}
function write(name: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(name)
    else window.localStorage.setItem(name, value)
  } catch {
    // not kept: a resync then only re-posts a subscription the browser still holds
  }
}

export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

export function permission(): NotificationPermission | 'unsupported' {
  return pushSupported() ? Notification.permission : 'unsupported'
}

/** The URL-safe base64 key the service gives, as the bytes `subscribe` wants. */
export function keyBytes(key: string): Uint8Array<ArrayBuffer> {
  const padded = key
    .replace(/-/g, '+')
    .replace(/_/g, '/')
    .padEnd(Math.ceil(key.length / 4) * 4, '=')
  const raw = atob(padded)
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i)
  return out
}

function sameKey(held: ArrayBuffer | null | undefined, wanted: Uint8Array): boolean {
  if (!held) return false
  const a = new Uint8Array(held)
  if (a.length !== wanted.length) return false
  return a.every((byte, i) => byte === wanted[i])
}

function newRequestId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`
  }
}

async function registration(): Promise<ServiceWorkerRegistration | null> {
  return registerFamilyWorker()
}

/** Tell the service about a subscription the browser holds. */
async function post(sub: PushSubscription, replaces: string | null): Promise<void> {
  const json = sub.toJSON()
  const p256dh = json.keys?.p256dh
  const auth = json.keys?.auth
  if (!json.endpoint || !p256dh || !auth) {
    throw new FamilyError(
      'invalid',
      'This phone gave notifications an address the family view cannot use.'
    )
  }
  await familyApi.pushSubscribe({
    endpoint: json.endpoint,
    keys: { p256dh, auth },
    replaces: replaces && replaces !== json.endpoint ? replaces : null,
    request_id: newRequestId(),
  })
  write(LAST, json.endpoint)
}

/** Whether this phone is receiving notifications now: permission, and a subscription held. */
export async function pushStatus(): Promise<PushStatus> {
  if (!pushSupported()) return 'unsupported'
  if (Notification.permission === 'denied') return 'blocked'
  if (Notification.permission !== 'granted') return 'off'
  const reg = await registration()
  const sub = await reg?.pushManager.getSubscription()
  return sub ? 'on' : 'off'
}

/**
 * Turn notifications on for this phone. Call it from a tap: it is what asks Chrome's permission.
 * Resolves to the status afterwards; throws a `FamilyError` in the viewer's words if the service
 * refused the subscription (the browser's is then dropped, so the toggle never lies).
 */
export async function enablePush(): Promise<PushStatus> {
  if (!pushSupported()) return 'unsupported'
  if (Notification.permission === 'denied') return 'blocked'
  const asked =
    Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission()
  if (asked === 'denied') return 'blocked'
  if (asked !== 'granted') return 'off'
  const reg = await registration()
  if (!reg) return 'unsupported'
  const { key } = await familyApi.pushKey()
  const wanted = keyBytes(key)
  let sub = await reg.pushManager.getSubscription()
  let replaces: string | null = null
  if (sub && !sameKey(sub.options.applicationServerKey, wanted)) {
    replaces = sub.endpoint
    await sub.unsubscribe()
    sub = null
  }
  const made =
    sub ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: wanted }))
  try {
    await post(made, replaces ?? read(LAST))
  } catch (error) {
    if (!sub) await made.unsubscribe().catch(() => false)
    throw error
  }
  write(FLAG, 'on')
  return 'on'
}

/** Turn notifications off for this phone: the service first, then the browser. */
export async function disablePush(): Promise<void> {
  const reg = pushSupported() ? await registration() : null
  const sub = await reg?.pushManager.getSubscription()
  if (sub) {
    await familyApi.pushUnsubscribe(sub.endpoint)
    await sub.unsubscribe()
  }
  write(FLAG, null)
  write(LAST, null)
}

/**
 * On every open: post the subscription the browser holds, when permission is granted. Resolves
 * to what it did, for a test; it never throws, because a failed re-sync only means the next
 * open tries again.
 */
export async function resyncPush(): Promise<'posted' | 'remade' | 'none' | 'failed'> {
  try {
    if (!pushSupported() || Notification.permission !== 'granted') return 'none'
    const reg = await registration()
    if (!reg) return 'none'
    let sub = await reg.pushManager.getSubscription()
    if (sub) {
      await post(sub, read(LAST))
      return 'posted'
    }
    // The browser lost it (the phone was reset, storage cleared by a cleaner): made again, but
    // only where the member had turned notifications on, never for one who turned them off.
    if (read(FLAG) !== 'on') return 'none'
    const { key } = await familyApi.pushKey()
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: keyBytes(key),
    })
    await post(sub, read(LAST))
    return 'remade'
  } catch {
    return 'failed'
  }
}

let resynced = false

/** For tests only: forget that this page has already re-synced. */
export function resetResyncForTests(): void {
  resynced = false
}

/**
 * The re-sync on open, once per page load and only while signed in: the shell calls it with
 * `enabled` true once the member is known. A phone that signs out and in again on the same page
 * load posts again.
 */
export function usePushResync(enabled: boolean): void {
  useEffect(() => {
    if (!enabled) {
      resynced = false
      return
    }
    if (resynced) return
    resynced = true
    void resyncPush()
  }, [enabled])
}
