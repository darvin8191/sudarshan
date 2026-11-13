// SUDARSHAN P6 D1 (M10.6): registers the family service worker, from the family shell only.
//
// The worker lives at /sw.js and is registered with scope /family/ (cross-design Y26): it never
// stands in front of the owner's pages, the trading screens included. main.tsx does not call
// this; FamilyLayout does, so the owner's bundle path never reaches a registration.

export const WORKER_URL = '/sw.js'
export const FAMILY_SCOPE = '/family/'

/** Registered once per page, however often the layout mounts. */
let registered: Promise<ServiceWorkerRegistration | null> | null = null

/** Whether the page is inside the family app (its scope). */
export function inFamilyScope(pathname: string): boolean {
  return pathname === '/family' || pathname.startsWith(FAMILY_SCOPE)
}

function afterLoad(): Promise<void> {
  if (document.readyState === 'complete') return Promise.resolve()
  return new Promise((resolve) => window.addEventListener('load', () => resolve(), { once: true }))
}

/**
 * Register the worker after the page has loaded, so it never competes with the first screen.
 * Resolves to the registration, or null where there is no service worker support, the page is
 * not in the family app, or the browser refused. Never throws: a phone without a worker simply
 * has no offline page, and every screen works as before.
 */
export function registerFamilyWorker(): Promise<ServiceWorkerRegistration | null> {
  if (registered) return registered
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) {
    return Promise.resolve(null)
  }
  if (!inFamilyScope(window.location.pathname)) return Promise.resolve(null)
  registered = afterLoad()
    .then(() => navigator.serviceWorker.register(WORKER_URL, { scope: FAMILY_SCOPE }))
    .catch(() => null)
  return registered
}

/** For tests only: forget the registration made on this page. */
export function resetRegistrationForTests(): void {
  registered = null
}
