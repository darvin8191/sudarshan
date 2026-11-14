/**
 * The service worker, run as the browser runs it: its source is evaluated against a fake worker
 * scope, a fake cache and a fetch the test controls. Rule 7: a cached answer from the API is an
 * old number shown as current, so the cases that matter most are the ones that cache nothing.
 */
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const SOURCE = readFileSync(join(process.cwd(), 'public', 'sw.js'), 'utf8')
const ORIGIN = 'https://sudarshan.example'

type Handler = (event: Record<string, unknown>) => void

interface World {
  handlers: Map<string, Handler>
  store: Map<string, Map<string, Response>>
  fetch: ReturnType<typeof vi.fn>
  self: Record<string, unknown>
  shown: Array<{ title: string; options: Record<string, unknown> }>
  clients: {
    matchAll: ReturnType<typeof vi.fn>
    openWindow: ReturnType<typeof vi.fn>
    claim: ReturnType<typeof vi.fn>
  }
  /** What fetch events asked to finish after their answer (event.waitUntil). */
  behind: Array<Promise<unknown>>
}

function keyOf(request: RequestInfo | URL): string {
  const raw =
    typeof request === 'string' ? request : 'url' in request ? request.url : String(request)
  return new URL(raw, ORIGIN).toString()
}

function boot(): World {
  const handlers = new Map<string, Handler>()
  const store = new Map<string, Map<string, Response>>()
  const shown: World['shown'] = []
  const clients = {
    matchAll: vi.fn().mockResolvedValue([]),
    openWindow: vi.fn().mockResolvedValue(undefined),
    claim: vi.fn().mockResolvedValue(undefined),
  }
  const caches = {
    async open(name: string) {
      if (!store.has(name)) store.set(name, new Map())
      const bucket = store.get(name) as Map<string, Response>
      return {
        async match(request: RequestInfo | URL) {
          return bucket.get(keyOf(request))?.clone()
        },
        async put(request: RequestInfo | URL, response: Response) {
          bucket.set(keyOf(request), response)
        },
        async add(request: RequestInfo | URL) {
          bucket.set(keyOf(request), await fetchMock(request))
        },
        async keys() {
          return [...bucket.keys()].map((url) => new Request(url))
        },
        async delete(request: RequestInfo | URL) {
          return bucket.delete(keyOf(request))
        },
      }
    },
    async keys() {
      return [...store.keys()]
    },
    async delete(name: string) {
      return store.delete(name)
    },
  }
  const fetchMock = vi.fn(
    async (_request: RequestInfo | URL, _init?: RequestInit) =>
      new Response('fresh', { status: 200 })
  )
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (type: string, handler: Handler) => handlers.set(type, handler),
    skipWaiting: vi.fn().mockResolvedValue(undefined),
    clients,
    registration: {
      showNotification: async (title: string, options: Record<string, unknown>) => {
        shown.push({ title, options })
      },
    },
  }
  new Function('self', 'caches', 'fetch', 'Response', 'URL', SOURCE)(
    self,
    caches,
    fetchMock,
    Response,
    URL
  )
  return { handlers, store, fetch: fetchMock, self, shown, clients, behind: [] }
}

/** Dispatch a fetch event; resolves to the response the worker gave, or null when it passed. */
async function dispatch(
  world: World,
  path: string,
  init: { mode?: string; method?: string } = {}
): Promise<Response | null> {
  let answer: Promise<Response> | null = null
  world.handlers.get('fetch')?.({
    request: {
      url: `${ORIGIN}${path}`,
      method: init.method ?? 'GET',
      mode: init.mode ?? 'cors',
      clone() {
        return this
      },
    },
    respondWith(promise: Promise<Response>) {
      answer = promise
    },
    waitUntil(promise: Promise<unknown>) {
      world.behind.push(promise)
    },
  })
  return answer ? await answer : null
}

/** Everything the fetch events asked to finish after their answers. */
async function behind(world: World) {
  while (world.behind.length) await world.behind.shift()
}

/** A built shell: its script, a preload and its styles, as Vite writes them. */
function shellOf(...assets: string[]) {
  const tags = assets.map((path) =>
    path.endsWith('.css')
      ? `<link rel="stylesheet" crossorigin href="${path}">`
      : `<script type="module" crossorigin src="${path}"></script>`
  )
  return new Response(`<!doctype html><html><head>${tags.join('')}</head></html>`, {
    status: 200,
    headers: { 'content-type': 'text/html; charset=utf-8' },
  })
}

function kept(world: World): string[] {
  return [...(world.store.get('sudarshan-shell-v1')?.keys() ?? [])].map((url) =>
    url.replace(ORIGIN, '')
  )
}

async function activate(world: World) {
  let pending: Promise<unknown> = Promise.resolve()
  world.handlers.get('activate')?.({
    waitUntil: (p: Promise<unknown>) => {
      pending = p
    },
  })
  await pending
}

const basic = (body: string) =>
  Object.defineProperty(new Response(body, { status: 200 }), 'type', { value: 'basic' })

async function install(world: World) {
  let pending: Promise<unknown> = Promise.resolve()
  world.handlers.get('install')?.({
    waitUntil: (p: Promise<unknown>) => {
      pending = p
    },
  })
  await pending
}

let world: World
beforeEach(() => {
  world = boot()
})

describe('install and activate', () => {
  it('precaches the offline page and the icons', async () => {
    await install(world)
    const kept = [...(world.store.get('sudarshan-shell-v1')?.keys() ?? [])]
    expect(kept).toContain(`${ORIGIN}/offline.html`)
    expect(kept).toContain(`${ORIGIN}/images/badge-96.png`)
  })

  it('deletes every other cache and takes the open pages', async () => {
    world.store.set('old-cache', new Map())
    world.store.set('sudarshan-shell-v1', new Map())
    let pending: Promise<unknown> = Promise.resolve()
    world.handlers.get('activate')?.({
      waitUntil: (p: Promise<unknown>) => {
        pending = p
      },
    })
    await pending
    expect([...world.store.keys()]).toEqual(['sudarshan-shell-v1'])
    expect(world.clients.claim).toHaveBeenCalled()
  })
})

describe('navigations: network first', () => {
  it('answers with the network, kept nowhere', async () => {
    const answer = await dispatch(world, '/family/positions', { mode: 'navigate' })
    expect(await answer?.text()).toBe('fresh')
    expect(world.store.size).toBe(0)
  })

  it('shows the offline page only when the fetch itself throws', async () => {
    await install(world)
    world.fetch.mockRejectedValueOnce(new TypeError('network down'))
    const answer = await dispatch(world, '/family/positions', { mode: 'navigate' })
    expect(answer?.status).toBe(200)
    expect(world.fetch).toHaveBeenCalledWith(expect.objectContaining({ url: expect.any(String) }))
  })

  it.each([
    401, 403, 404, 500, 502, 503,
  ])('never shows the offline page for a %i', async (status) => {
    await install(world)
    world.fetch.mockResolvedValueOnce(new Response('the server says', { status }))
    const answer = await dispatch(world, '/family/holdings', { mode: 'navigate' })
    expect(answer?.status).toBe(status)
    expect(await answer?.text()).toBe('the server says')
  })
})

describe('what is never cached (rule 7)', () => {
  const FORBIDDEN = [
    '/family/api/books/positions',
    '/family/api/me',
    '/api/v1/positionbook',
    '/auth/session-status',
    '/sudarshan/risk/state',
    '/socket.io/?EIO=4',
    '/ws',
    '/family/ws',
  ]
  it.each(FORBIDDEN)('passes %s straight through, answered and kept by nothing', async (path) => {
    // Not an asset or an image either way: the worker steps aside and nothing is kept.
    expect(await dispatch(world, path)).toBeNull()
    expect(world.store.size).toBe(0)
  })

  it('never answers a non-GET', async () => {
    expect(await dispatch(world, '/assets/app-1a2b3c.js', { method: 'POST' })).toBeNull()
  })

  it('does not touch a request for another origin', async () => {
    let answered = false
    world.handlers.get('fetch')?.({
      request: { url: 'https://elsewhere.example/assets/x.js', method: 'GET', mode: 'cors' },
      respondWith: () => {
        answered = true
      },
    })
    expect(answered).toBe(false)
  })

  it('a navigation to a path under a never-cached prefix is not served from a cache either', async () => {
    const answer = await dispatch(world, '/family/api/me', { mode: 'navigate' })
    expect(answer).toBeNull()
  })

  it('treats /api on a path boundary, so the /apikey page is an ordinary page', async () => {
    const answer = await dispatch(world, '/apikey', { mode: 'navigate' })
    expect(answer).not.toBeNull()
  })
})

describe('assets and images: cache first', () => {
  it('fetches an asset once and serves it from the cache after', async () => {
    world.fetch.mockResolvedValue(
      Object.defineProperty(new Response('js', { status: 200 }), 'type', { value: 'basic' })
    )
    await dispatch(world, '/assets/app-1a2b3c.js')
    const second = await dispatch(world, '/assets/app-1a2b3c.js')
    expect(await second?.text()).toBe('js')
    expect(world.fetch).toHaveBeenCalledTimes(1)
  })

  it('does not keep a failed asset answer', async () => {
    world.fetch.mockResolvedValue(new Response('gone', { status: 404 }))
    await dispatch(world, '/assets/missing.js')
    await dispatch(world, '/assets/missing.js')
    expect(world.fetch).toHaveBeenCalledTimes(2)
  })

  it('goes to the network first for a manifest, and falls back to its last copy', async () => {
    world.fetch.mockResolvedValueOnce(new Response('{"v":1}', { status: 200 }))
    await dispatch(world, '/images/sudarshan-family.webmanifest')
    world.fetch.mockResolvedValueOnce(new Response('{"v":2}', { status: 200 }))
    const fresh = await dispatch(world, '/images/sudarshan-family.webmanifest')
    expect(await fresh?.text()).toBe('{"v":2}')
    world.fetch.mockRejectedValueOnce(new TypeError('down'))
    const offline = await dispatch(world, '/images/sudarshan-family.webmanifest')
    expect(await offline?.text()).toBe('{"v":2}')
  })

  it('passes anything else through', async () => {
    expect(await dispatch(world, '/favicon.ico')).toBeNull()
  })
})

describe('images: the kept copy at once, a fresh one behind it (review L5)', () => {
  it('fetches an image the first time and keeps it', async () => {
    world.fetch.mockResolvedValueOnce(basic('icon v1'))
    expect(await (await dispatch(world, '/images/badge-96.png'))?.text()).toBe('icon v1')
    await behind(world)
    expect(kept(world)).toEqual(['/images/badge-96.png'])
  })

  it('answers from the cache and keeps the fresh copy for the next time', async () => {
    world.fetch.mockResolvedValueOnce(basic('icon v1'))
    await dispatch(world, '/images/family-offline.js')
    world.fetch.mockResolvedValueOnce(basic('icon v2'))
    expect(await (await dispatch(world, '/images/family-offline.js'))?.text()).toBe('icon v1')
    await behind(world)
    world.fetch.mockRejectedValueOnce(new TypeError('down'))
    expect(await (await dispatch(world, '/images/family-offline.js'))?.text()).toBe('icon v2')
    await behind(world) // a refresh that failed keeps the copy it had
    world.fetch.mockResolvedValueOnce(basic('icon v3'))
    expect(await (await dispatch(world, '/images/family-offline.js'))?.text()).toBe('icon v2')
    expect(world.fetch).toHaveBeenCalledTimes(4)
  })

  it('never keeps an image answer that failed', async () => {
    world.fetch.mockResolvedValue(new Response('gone', { status: 404 }))
    await dispatch(world, '/images/none.png')
    await behind(world)
    expect(kept(world)).toEqual([])
  })
})

describe('the cache does not grow with every release (review L5)', () => {
  async function fill(...paths: string[]) {
    for (const path of paths) {
      world.fetch.mockResolvedValueOnce(basic(path))
      await dispatch(world, path)
    }
  }

  it('on activate, deletes every asset the current shell does not name', async () => {
    await fill('/assets/index-old1.js', '/assets/index-new1.js', '/assets/style-new1.css')
    await fill('/images/badge-96.png')
    await behind(world)
    world.fetch.mockResolvedValueOnce(shellOf('/assets/index-new1.js', '/assets/style-new1.css'))
    await activate(world)
    expect(kept(world).sort()).toEqual([
      '/assets/index-new1.js',
      '/assets/style-new1.css',
      '/images/badge-96.png',
    ])
    expect(world.fetch).toHaveBeenLastCalledWith('/family/', expect.anything())
    expect(world.clients.claim).toHaveBeenCalled()
  })

  it('deletes nothing when the shell cannot be read, or names no asset', async () => {
    await fill('/assets/index-old1.js')
    world.fetch.mockRejectedValueOnce(new TypeError('offline'))
    await activate(world)
    world.fetch.mockResolvedValueOnce(new Response('<p>Not a shell</p>', { status: 200 }))
    await activate(world)
    world.fetch.mockResolvedValueOnce(new Response('busy', { status: 503 }))
    await activate(world)
    expect(kept(world)).toEqual(['/assets/index-old1.js'])
  })

  it('a navigation that brings a new release trims and fetches the precached files again', async () => {
    await install(world)
    await fill('/assets/index-old1.js', '/assets/chunk-old1.js')
    const precacheFetches = () =>
      world.fetch.mock.calls.filter(([what]) => what === '/offline.html').length
    const before = precacheFetches()
    world.fetch.mockResolvedValueOnce(shellOf('/assets/index-new2.js'))
    const answer = await dispatch(world, '/family/positions', { mode: 'navigate' })
    expect(await answer?.text()).toContain('/assets/index-new2.js')
    await behind(world)
    expect(kept(world).filter((path) => path.startsWith('/assets/'))).toEqual([])
    expect(precacheFetches()).toBe(before + 1)
    await fill('/assets/index-new2.js', '/assets/chunk-new2.js') // the release loads its chunks
    world.fetch.mockResolvedValueOnce(shellOf('/assets/index-new2.js'))
    await dispatch(world, '/family', { mode: 'navigate' }) // the same release, opened again
    await behind(world)
    expect(
      kept(world)
        .filter((path) => path.startsWith('/assets/'))
        .sort()
    ).toEqual(['/assets/chunk-new2.js', '/assets/index-new2.js'])
    expect(precacheFetches()).toBe(before + 1)
  })
})

function pushEvent(payload: unknown) {
  let pending: Promise<unknown> = Promise.resolve()
  return {
    event: {
      data: { json: () => payload },
      waitUntil: (p: Promise<unknown>) => {
        pending = p
      },
    },
    done: () => pending,
  }
}

describe('push', () => {
  it('shows a notification with the badge, the icon and its target', async () => {
    const { event, done } = pushEvent({
      title: 'A title',
      body: 'A body',
      tag: 'digest-20261113',
      url: '/family/positions',
      lang: 'en',
    })
    world.handlers.get('push')?.(event)
    await done()
    expect(world.shown).toHaveLength(1)
    expect(world.shown[0].title).toBe('A title')
    expect(world.shown[0].options).toMatchObject({
      body: 'A body',
      tag: 'digest-20261113',
      renotify: false,
      badge: '/images/badge-96.png',
      icon: '/images/android-chrome-192x192.png',
      data: { url: '/family/positions' },
      lang: 'en',
    })
  })

  it('keeps a target inside the family app, whatever the payload says', async () => {
    for (const url of ['https://evil.example/family/', '/dashboard', '//evil.example/x']) {
      const { event, done } = pushEvent({ title: 't', url })
      world.handlers.get('push')?.(event)
      await done()
    }
    expect(world.shown.map((n) => (n.options.data as { url: string }).url)).toEqual([
      '/family/',
      '/family/',
      '/family/',
    ])
  })

  it('still shows something for a payload it cannot read', async () => {
    const event = {
      data: {
        json: () => {
          throw new Error('not json')
        },
      },
      waitUntil: vi.fn(),
    }
    world.handlers.get('push')?.(event)
    await (event.waitUntil.mock.calls[0][0] as Promise<unknown>)
    expect(world.shown[0].title).toContain('SUDARSHAN')
  })
})

describe('notificationclick', () => {
  async function click(url: string) {
    const close = vi.fn()
    let pending: Promise<unknown> = Promise.resolve()
    world.handlers.get('notificationclick')?.({
      notification: { close, data: { url } },
      waitUntil: (p: Promise<unknown>) => {
        pending = p
      },
    })
    await pending
    return close
  }

  it('closes the notification and opens the app when none is open', async () => {
    const close = await click('/family/holdings')
    expect(close).toHaveBeenCalled()
    expect(world.clients.openWindow).toHaveBeenCalledWith('/family/holdings')
  })

  // Review M7 (v0.12.0): `/family` is outside the installed app's scope. Fails on the old worker.
  it('opens the home at /family/, whether the message names /family, nothing or another site', async () => {
    for (const url of ['/family', '', 'https://elsewhere.invalid/x', '/dashboard']) {
      world.clients.openWindow.mockClear()
      await click(url)
      expect(world.clients.openWindow).toHaveBeenCalledWith('/family/')
    }
    world.clients.openWindow.mockClear()
    await click('/family?from=digest')
    expect(world.clients.openWindow).toHaveBeenCalledWith('/family/?from=digest')
  })

  it('focuses an open family window instead of opening another', async () => {
    const focus = vi.fn().mockResolvedValue(undefined)
    world.clients.matchAll.mockResolvedValue([
      { url: `${ORIGIN}/dashboard`, focus: vi.fn() },
      { url: `${ORIGIN}/family/positions`, focus },
    ])
    await click('/family/positions')
    expect(focus).toHaveBeenCalled()
    expect(world.clients.openWindow).not.toHaveBeenCalled()
  })
})

describe('the file itself', () => {
  it('names only files the build has (a missing one would count toward a ban)', () => {
    const named = new Set<string>()
    for (const found of SOURCE.matchAll(/'(\/(?:images\/[^']+|offline\.html))'/g)) {
      named.add(found[1])
    }
    expect([...named]).toContain('/offline.html')
    expect([...named]).toContain('/images/family-offline.js')
    for (const path of named) {
      expect(existsSync(join(process.cwd(), 'public', path)), path).toBe(true)
    }
  })

  it('lists the prefixes the design names, and no more', () => {
    const line = /const NEVER = (\[[^\]]*\])/.exec(SOURCE)?.[1] ?? ''
    expect(JSON.parse(line.replaceAll("'", '"'))).toEqual([
      '/family/api',
      '/api',
      '/auth',
      '/sudarshan',
      '/socket.io',
      '/ws',
    ])
  })
})
