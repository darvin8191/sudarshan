/*
 * SUDARSHAN family service worker (P6 D1, M10.6).
 *
 * Plain JavaScript, no bundler, no Workbox. Vite copies public/ to the dist root and the server
 * answers it at /sw.js with no-cache. It is registered only from the family shell, with scope
 * /family/ (cross-design Y26): the owner's pages never run behind it.
 *
 * Rule 7 shapes it. A cached answer from the API is an old number shown as current, so nothing
 * under /family/api, /api, /auth, /sudarshan, /socket.io or /ws is ever cached or answered here:
 * those requests go straight to the network, untouched.
 *
 *   navigations   network first. The offline page is shown only when the network itself fails,
 *                 never for a 4xx or 5xx answer: the server's own words are the right ones.
 *   /assets/*     cache first (content-hashed, so a hit is never stale).
 *   /images/*     cache first, except .webmanifest files, which are network first.
 *   anything else passes through.
 */
const CACHE = 'sudarshan-shell-v1'
const OFFLINE = '/offline.html'
const PRECACHE = [
  '/images/android-chrome-192x192.png',
  '/images/android-chrome-512x512.png',
  '/images/badge-96.png',
  '/images/family-offline.js',
]
const NEVER = ['/family/api', '/api', '/auth', '/sudarshan', '/socket.io', '/ws']
const HOME = '/family/'
const SUBSCRIBE = '/family/api/push/subscriptions'
const CSRF_COOKIE = '__Host-sudarshan_family_csrf'

/** Whether a path is one of the prefixes (on a path boundary: /api is not /apikey). */
function under(path, prefixes) {
  return prefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))
}

/** The request's path when it is for this origin, else null. */
function pathOf(request) {
  const url = new URL(request.url)
  return url.origin === self.location.origin ? url.pathname : null
}

/** Never cached: kept as a function so a test pins the list itself. */
function neverCached(path) {
  return under(path, NEVER)
}

async function networkFirstOffline(request) {
  try {
    return await fetch(request)
  } catch (_failure) {
    const cache = await caches.open(CACHE)
    const page = await cache.match(OFFLINE)
    return page || Response.error()
  }
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE)
  try {
    const fresh = await fetch(request)
    if (fresh.ok) await cache.put(request, fresh.clone())
    return fresh
  } catch (failure) {
    const kept = await cache.match(request)
    if (kept) return kept
    throw failure
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE)
  const kept = await cache.match(request)
  if (kept) return kept
  const fresh = await fetch(request)
  if (fresh.ok && fresh.type === 'basic') await cache.put(request, fresh.clone())
  return fresh
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE)
      await cache.add(OFFLINE)
      // The icons are a convenience: one that cannot be fetched now must not stop the install.
      await Promise.all(PRECACHE.map((path) => cache.add(path).catch(() => undefined)))
      await self.skipWaiting()
    })()
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys()
      await Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name)))
      await self.clients.claim()
    })()
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return
  const path = pathOf(request)
  if (path === null || neverCached(path)) return
  if (request.mode === 'navigate') {
    event.respondWith(networkFirstOffline(request))
    return
  }
  if (path.endsWith('.webmanifest')) {
    event.respondWith(networkFirst(request))
    return
  }
  if (under(path, ['/assets', '/images'])) {
    event.respondWith(cacheFirst(request))
  }
})

/** A target inside the family app on this origin, else its home. */
function targetOf(value) {
  try {
    const url = new URL(value, self.location.origin)
    if (url.origin === self.location.origin && (url.pathname === '/family' || url.pathname.startsWith(HOME))) {
      return `${url.pathname}${url.search}`
    }
  } catch (_unreadable) {
    // fall through to home
  }
  return HOME
}

self.addEventListener('push', (event) => {
  let payload = {}
  try {
    payload = event.data ? event.data.json() : {}
  } catch (_unreadable) {
    payload = {}
  }
  const title = typeof payload.title === 'string' && payload.title ? payload.title : 'सुदर्शन · SUDARSHAN'
  const options = {
    body: typeof payload.body === 'string' ? payload.body : '',
    tag: typeof payload.tag === 'string' ? payload.tag : undefined,
    renotify: false,
    icon: '/images/android-chrome-192x192.png',
    badge: '/images/badge-96.png',
    data: { url: targetOf(payload.url) },
    lang: typeof payload.lang === 'string' ? payload.lang : undefined,
  }
  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = targetOf(event.notification.data && event.notification.data.url)
  event.waitUntil(
    (async () => {
      const open = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      const mine = open.find((client) => {
        try {
          const path = new URL(client.url).pathname
          return path === '/family' || path.startsWith(HOME)
        } catch (_unreadable) {
          return false
        }
      })
      if (mine) {
        if ('focus' in mine) await mine.focus()
        if ('navigate' in mine && new URL(mine.url).pathname !== url.split('?')[0]) {
          await mine.navigate(url)
        }
        return
      }
      await self.clients.openWindow(url)
    })()
  )
})

/**
 * Chrome does not always tell the page that its push subscription changed, so the worker
 * subscribes again itself when it can and tells the service which one it replaces. The page
 * also posts the current subscription on every open, which is the guarantee; this is the
 * quicker path. It needs the page's CSRF cookie, which only some browsers let a worker read.
 */
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil(
    (async () => {
      try {
        const old = event.oldSubscription || null
        const key = old && old.options ? old.options.applicationServerKey : null
        if (!key || !self.cookieStore) return
        const cookie = await self.cookieStore.get(CSRF_COOKIE)
        if (!cookie || !cookie.value) return
        const made =
          event.newSubscription ||
          (await self.registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: key,
          }))
        const json = made.toJSON()
        await fetch(SUBSCRIBE, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-Family-CSRF': cookie.value },
          body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys, replaces: old ? old.endpoint : null }),
        })
      } catch (_failure) {
        // The next open of the app posts the subscription again.
      }
    })()
  )
})
