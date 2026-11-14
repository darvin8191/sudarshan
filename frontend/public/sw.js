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
 *   /images/*     stale-while-revalidate: the kept copy at once, a fresh one fetched behind it,
 *                 so a changed icon or offline script reaches the phone; .webmanifest files are
 *                 network first.
 *   anything else passes through.
 *
 * The cache does not grow with every release (review L5). On activate, and whenever a navigation
 * brings a shell that names an asset the cache lacks (a new release: this file itself rarely
 * changes, so activate alone would rarely run), every /assets/ entry the current shell does not
 * name is deleted and the precached files are fetched again. A page that names no asset is not a
 * shell, and nothing is deleted on its word. A chunk the shell loads later is fetched again when
 * it is next needed (the browser's own cache usually still holds it).
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
/** How long activate waits for the shell it trims by (fetches wait while it activates). */
const SHELL_WAIT_MS = 3000
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

/** The /assets/ files a shell names: its script, its preloads and its styles. */
function assetsOf(html) {
  const named = new Set()
  for (const found of html.matchAll(/(?:src|href)="(\/assets\/[^"?#]+)"/g)) named.add(found[1])
  return named
}

/** Delete every /assets/ entry the shell does not name; how many went. */
async function trimAssets(named) {
  if (named.size === 0) return 0
  const cache = await caches.open(CACHE)
  let gone = 0
  for (const request of await cache.keys()) {
    const path = pathOf(request)
    if (path !== null && under(path, ['/assets']) && !named.has(path)) {
      await cache.delete(request)
      gone += 1
    }
  }
  return gone
}

/** Fetch the offline page, the icons and the offline script again; a failure keeps the old copy. */
async function refreshPrecache() {
  const cache = await caches.open(CACHE)
  await Promise.all(
    [OFFLINE, ...PRECACHE].map(async (path) => {
      try {
        const fresh = await fetch(path, { cache: 'no-cache' })
        if (fresh.ok) await cache.put(path, fresh)
      } catch (_failure) {
        // kept as it was; tried again at the next release
      }
    })
  )
}

/** A shell that names an asset the cache lacks is a new release: trim, and refresh the rest. */
async function settleRelease(shell) {
  const named = assetsOf(await shell.text())
  if (named.size === 0) return
  const cache = await caches.open(CACHE)
  const kept = new Set((await cache.keys()).map(pathOf))
  if ([...named].every((path) => kept.has(path))) return // this release, opened again
  await trimAssets(named)
  await refreshPrecache()
}

async function networkFirstOffline(event) {
  try {
    const fresh = await fetch(event.request)
    const type = fresh.headers.get('content-type') || ''
    if (fresh.ok && type.includes('text/html')) {
      event.waitUntil(settleRelease(fresh.clone()).catch(() => undefined))
    }
    return fresh
  } catch (_failure) {
    const cache = await caches.open(CACHE)
    const page = await cache.match(OFFLINE)
    return page || Response.error()
  }
}

/** The current shell, for activate: a short wait, and null rather than an error. */
async function currentShell() {
  const stop = new AbortController()
  const timer = setTimeout(() => stop.abort(), SHELL_WAIT_MS)
  try {
    const shell = await fetch(HOME, { cache: 'no-cache', signal: stop.signal })
    return shell.ok ? await shell.text() : null
  } catch (_failure) {
    return null
  } finally {
    clearTimeout(timer)
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

/** The kept copy at once, and a fresh one fetched behind it for the next time. */
async function staleWhileRevalidate(event) {
  const { request } = event
  const cache = await caches.open(CACHE)
  const kept = await cache.match(request)
  const refresh = (async () => {
    const fresh = await fetch(request)
    if (fresh.ok && fresh.type === 'basic') await cache.put(request, fresh.clone())
    return fresh
  })()
  if (!kept) return refresh
  event.waitUntil(refresh.catch(() => undefined))
  return kept
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
      const shell = await currentShell()
      if (shell !== null) await trimAssets(assetsOf(shell))
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
    event.respondWith(networkFirstOffline(event))
    return
  }
  if (path.endsWith('.webmanifest')) {
    event.respondWith(networkFirst(request))
    return
  }
  if (under(path, ['/assets'])) {
    event.respondWith(cacheFirst(request))
    return
  }
  if (under(path, ['/images'])) {
    event.respondWith(staleWhileRevalidate(event))
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
