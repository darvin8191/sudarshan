// SUDARSHAN P6 D8: Web Push for a family phone, against a fake browser (a worker registration, a
// push manager, a permission) and a fake service. Synthetic endpoints and keys only. Nothing here
// is allowed to ask Chrome for permission except enablePush, which a tap calls.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, renderHook, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FamilyLayout } from '@/sudarshan/family/FamilyLayout'
import { resetFamilyManagerForTests } from '@/sudarshan/family/familyManager'
import { FakeSocket, fail, meOf, ok, routeFetch } from '@/sudarshan/family/testing/fixtures'
import { stubCookies } from '@/sudarshan/family/testing/harness'
import {
  disablePush,
  enablePush,
  keyBytes,
  pushStatus,
  resetResyncForTests,
  resyncPush,
  usePushResync,
} from './push'
import { resetRegistrationForTests } from './register'

// 1..6 as the service's URL-safe key
const KEY = 'AQIDBAUG'
const KEY_BYTES = [1, 2, 3, 4, 5, 6]

interface FakeSub {
  endpoint: string
  options: { applicationServerKey: ArrayBuffer | null }
  toJSON: () => { endpoint: string; keys: { p256dh: string; auth: string } }
  unsubscribe: ReturnType<typeof vi.fn>
}

function subOf(endpoint: string, key: number[] = KEY_BYTES): FakeSub {
  return {
    endpoint,
    options: { applicationServerKey: new Uint8Array(key).buffer },
    toJSON: () => ({ endpoint, keys: { p256dh: `p256-${endpoint}`, auth: `auth-${endpoint}` } }),
    unsubscribe: vi.fn(async () => true),
  }
}

let held: FakeSub | null
let permission: NotificationPermission
let askAnswer: NotificationPermission
let manager: { getSubscription: ReturnType<typeof vi.fn>; subscribe: ReturnType<typeof vi.fn> }
let ask: ReturnType<typeof vi.fn>

const SUBSCRIBE = '/family/api/push/subscriptions'
const REMOVE = '/family/api/push/subscriptions/remove'
const KEY_URL = '/family/api/push/key'

function service(over: Record<string, unknown> = {}) {
  return routeFetch({
    [REMOVE]: ok({ ok: true, words: null }),
    [SUBSCRIBE]: ok({ ok: true, words: null }),
    [KEY_URL]: ok({ key: KEY }),
    ...over,
  })
}

beforeEach(() => {
  held = null
  permission = 'granted'
  askAnswer = 'granted'
  manager = {
    getSubscription: vi.fn(async () => held),
    subscribe: vi.fn(async () => {
      held = subOf('https://push.example.invalid/new')
      return held
    }),
  }
  ask = vi.fn(async () => {
    permission = askAnswer
    return askAnswer
  })
  resetRegistrationForTests()
  resetResyncForTests()
  window.localStorage.clear()
  window.history.replaceState({}, '', '/family/settings')
  stubCookies('__Host-sudarshan_family_csrf=tok123')
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: { register: vi.fn(async () => ({ pushManager: manager })) },
  })
  vi.stubGlobal('PushManager', class {})
  vi.stubGlobal('Notification', {
    get permission() {
      return permission
    },
    requestPermission: ask,
  })
})

afterEach(() => {
  cleanup()
  resetFamilyManagerForTests()
  vi.unstubAllGlobals()
  delete (navigator as unknown as { serviceWorker?: unknown }).serviceWorker
  window.history.replaceState({}, '', '/')
})

describe('the key', () => {
  it('turns the service key into the bytes the browser wants', () => {
    expect([...keyBytes(KEY)]).toEqual(KEY_BYTES)
    expect([...keyBytes('-_8')]).toEqual([251, 255])
  })
})

describe('turning notifications on', () => {
  it('asks permission, reads the service key, subscribes with it and tells the service', async () => {
    permission = 'default'
    const router = service()
    expect(await enablePush()).toBe('on')
    expect(ask).toHaveBeenCalledTimes(1)
    const options = manager.subscribe.mock.calls[0][0]
    expect(options.userVisibleOnly).toBe(true)
    expect([...(options.applicationServerKey as Uint8Array)]).toEqual(KEY_BYTES)
    const post = router.calls.find((call) => call.url === SUBSCRIBE)
    expect(post?.method).toBe('POST')
    expect(post?.headers['X-Family-CSRF']).toBe('tok123')
    expect(post?.body).toMatchObject({
      endpoint: 'https://push.example.invalid/new',
      keys: {
        p256dh: 'p256-https://push.example.invalid/new',
        auth: 'auth-https://push.example.invalid/new',
      },
    })
    expect((post?.body as { request_id: string }).request_id.length).toBeGreaterThan(8)
    expect(router.urls().every((url) => url.startsWith('/family/api/'))).toBe(true)
    expect(window.localStorage.getItem('sd-family-push')).toBe('on')
  })

  it('does not ask again when permission is already granted', async () => {
    service()
    await enablePush()
    expect(ask).not.toHaveBeenCalled()
  })

  it('says blocked, and asks and subscribes nothing, when Chrome has it blocked', async () => {
    permission = 'denied'
    const router = service()
    expect(await enablePush()).toBe('blocked')
    expect(ask).not.toHaveBeenCalled()
    expect(manager.subscribe).not.toHaveBeenCalled()
    expect(router.calls).toHaveLength(0)
  })

  it('stays off when the member dismisses the question', async () => {
    permission = 'default'
    askAnswer = 'default'
    const router = service()
    expect(await enablePush()).toBe('off')
    expect(manager.subscribe).not.toHaveBeenCalled()
    expect(router.calls).toHaveLength(0)
  })

  it('says blocked when the member answers no', async () => {
    permission = 'default'
    askAnswer = 'denied'
    service()
    expect(await enablePush()).toBe('blocked')
  })

  it('replaces a subscription made with another key, naming the old one', async () => {
    held = subOf('https://push.example.invalid/old', [9, 9, 9])
    const old = held
    const router = service()
    expect(await enablePush()).toBe('on')
    expect(old.unsubscribe).toHaveBeenCalled()
    const post = router.calls.find((call) => call.url === SUBSCRIBE)
    expect(post?.body).toMatchObject({ replaces: 'https://push.example.invalid/old' })
  })

  it('drops the browser subscription again when the service refuses it', async () => {
    service({ [SUBSCRIBE]: fail('limit', 'That is more than the family view allows.') })
    await expect(enablePush()).rejects.toThrow('That is more than the family view allows.')
    expect(held?.unsubscribe).toHaveBeenCalled()
    expect(window.localStorage.getItem('sd-family-push')).toBeNull()
  })

  it('is unsupported, not an error, where the browser has no push', async () => {
    vi.stubGlobal('PushManager', undefined)
    delete (window as unknown as { PushManager?: unknown }).PushManager
    expect(await enablePush()).toBe('unsupported')
    expect(await pushStatus()).toBe('unsupported')
  })
})

describe('the re-sync on every open', () => {
  it('re-posts the subscription the browser holds, when permission is granted', async () => {
    held = subOf('https://push.example.invalid/held')
    const router = service()
    expect(await resyncPush()).toBe('posted')
    const post = router.calls.find((call) => call.url === SUBSCRIBE)
    expect(post?.body).toMatchObject({ endpoint: 'https://push.example.invalid/held' })
    // it asked for nothing else: no permission, no key
    expect(ask).not.toHaveBeenCalled()
    expect(router.urls()).toEqual([SUBSCRIBE])
  })

  it('does nothing before permission is granted, or when it is blocked', async () => {
    for (const state of ['default', 'denied'] as const) {
      permission = state
      held = subOf('https://push.example.invalid/held')
      const router = service()
      expect(await resyncPush()).toBe('none')
      expect(router.calls).toHaveLength(0)
      expect(ask).not.toHaveBeenCalled()
    }
  })

  it('names the endpoint it last posted when the browser has a different one now', async () => {
    window.localStorage.setItem('sd-family-push-endpoint', 'https://push.example.invalid/before')
    held = subOf('https://push.example.invalid/after')
    const router = service()
    await resyncPush()
    expect(router.calls.find((call) => call.url === SUBSCRIBE)?.body).toMatchObject({
      replaces: 'https://push.example.invalid/before',
    })
  })

  it('makes a lost subscription again, but only for a phone where notifications were turned on', async () => {
    window.localStorage.setItem('sd-family-push', 'on')
    const router = service()
    expect(await resyncPush()).toBe('remade')
    expect(manager.subscribe).toHaveBeenCalledTimes(1)
    expect(router.urls()).toEqual([KEY_URL, SUBSCRIBE])
  })

  it('leaves a phone that turned notifications off alone', async () => {
    const router = service()
    expect(await resyncPush()).toBe('none')
    expect(manager.subscribe).not.toHaveBeenCalled()
    expect(router.calls).toHaveLength(0)
  })

  it('is never an error: a failed post is "failed", and the next open tries again', async () => {
    held = subOf('https://push.example.invalid/held')
    service({ [SUBSCRIBE]: fail('busy', 'The family view is busy.') })
    expect(await resyncPush()).toBe('failed')
  })

  // Review L5 (v0.12.0): on a shared phone the second member was subscribed without a tap,
  // because the re-sync posted the browser's subscription for whoever signed in. The second
  // expectation fails on the old re-sync.
  it('posts for the member who turned notifications on, and not for another on the same phone', async () => {
    const router = service()
    const posts = () => router.urls().filter((url) => url === SUBSCRIBE).length
    permission = 'default'
    expect(await enablePush(1)).toBe('on')
    expect(window.localStorage.getItem('sd-family-push')).toBe('on:1')
    const after = posts()
    expect(await resyncPush(2)).toBe('none')
    expect(posts()).toBe(after)
    expect(await resyncPush(1)).toBe('posted')
    expect(posts()).toBe(after + 1)
  })

  it('still posts where the phone kept no flag, so a changed subscription is never lost', async () => {
    held = subOf('https://push.example.invalid/held')
    service()
    expect(window.localStorage.getItem('sd-family-push')).toBeNull()
    expect(await resyncPush(2)).toBe('posted')
  })

  it('runs once per open of the app while signed in, and again after signing in anew', async () => {
    held = subOf('https://push.example.invalid/held')
    const router = service()
    const posts = () => router.urls().filter((url) => url === SUBSCRIBE).length
    const { rerender } = renderHook(({ on }) => usePushResync(on), { initialProps: { on: false } })
    expect(posts()).toBe(0)
    rerender({ on: true })
    await waitFor(() => expect(posts()).toBe(1))
    rerender({ on: true })
    rerender({ on: true })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(posts()).toBe(1)
    rerender({ on: false })
    rerender({ on: true })
    await waitFor(() => expect(posts()).toBe(2))
  })

  it('is done by the family shell when the member is known', async () => {
    held = subOf('https://push.example.invalid/held')
    FakeSocket.reset()
    vi.stubGlobal('WebSocket', FakeSocket)
    const router = service({ '/family/api/me': ok(meOf('viewer')) })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/family']}>
          <Routes>
            <Route path="/family" element={<FamilyLayout />}>
              <Route index element={<p>Home</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )
    await screen.findByText('Home')
    await waitFor(() => expect(router.urls()).toContain(SUBSCRIBE))
  })
})

describe('turning notifications off', () => {
  it('tells the service, then drops the browser subscription, and stops the re-sync remaking it', async () => {
    held = subOf('https://push.example.invalid/held')
    const sub = held
    window.localStorage.setItem('sd-family-push', 'on')
    const router = service()
    await disablePush()
    const removal = router.calls.find((call) => call.url === REMOVE)
    expect(removal?.body).toEqual({ endpoint: 'https://push.example.invalid/held' })
    expect(sub.unsubscribe).toHaveBeenCalled()
    held = null
    expect(await resyncPush()).toBe('none')
    expect(manager.subscribe).not.toHaveBeenCalled()
  })

  it('keeps the browser subscription when the service could not be told', async () => {
    held = subOf('https://push.example.invalid/held')
    const sub = held
    service({ [REMOVE]: fail('busy', 'The family view is busy.') })
    await expect(disablePush()).rejects.toThrow()
    expect(sub.unsubscribe).not.toHaveBeenCalled()
  })

  it('reads on when a subscription is held and off when not', async () => {
    service()
    expect(await pushStatus()).toBe('off')
    held = subOf('https://push.example.invalid/held')
    expect(await pushStatus()).toBe('on')
    permission = 'denied'
    expect(await pushStatus()).toBe('blocked')
  })
})
