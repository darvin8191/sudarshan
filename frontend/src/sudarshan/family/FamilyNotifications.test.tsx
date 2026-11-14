// SUDARSHAN P6 D8: the Notifications card, the install offers, and the steps joining adds for
// them, against a fake browser and a fake service. Chrome's permission question must come only
// from a tap, and the install question likewise.
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetInstallForTests } from '@/sudarshan/pwa/install'
import { resetResyncForTests } from '@/sudarshan/pwa/push'
import { resetRegistrationForTests } from '@/sudarshan/pwa/register'
import { resetFamilyManagerForTests } from './familyManager'
import { Notifications } from './Notifications'
import FamilyJoin from './pages/FamilyJoin'
import FamilySettings from './pages/FamilySettings'
import { FamilyMeContext } from './session'
import { FakeSocket, fail, meOf, ok, routeFetch } from './testing/fixtures'
import { mountFamily, stubCookies, where } from './testing/harness'

const KEY = 'AQIDBAUG'
const SUBSCRIBE = '/family/api/push/subscriptions'
const SETTINGS_URL = '/family/api/settings'

let held: { endpoint: string; unsubscribe: () => Promise<boolean>; [k: string]: unknown } | null
let permission: NotificationPermission
let ask: ReturnType<typeof vi.fn>
let manager: { getSubscription: ReturnType<typeof vi.fn>; subscribe: ReturnType<typeof vi.fn> }

const SETTINGS = {
  language: 'en',
  quiet: { from: '21:00', to: '08:00', digest: true, alerts: false },
  notify: { digest_push: false, digest_telegram: true },
  telegram_linked: true,
  version: 7,
}

function device(over: Record<string, unknown> = {}) {
  return {
    id: 'dev-this',
    label: 'Android 15 · Chrome 130',
    created_at: 1,
    last_seen_at: 2,
    place: null,
    current: true,
    notifications: null,
    ...over,
  }
}

function service(extra: Record<string, unknown> = {}) {
  return routeFetch({
    '/family/api/devices': ok([device()]),
    [SETTINGS_URL]: (_url: string, init?: RequestInit) =>
      init?.method === 'PUT' ? ok({ ok: true, words: null }) : ok(SETTINGS),
    '/family/api/push/key': ok({ key: KEY }),
    [SUBSCRIBE]: ok({ ok: true, words: null }),
    '/family/api/push/subscriptions/remove': ok({ ok: true, words: null }),
    '/family/api/notify/test': ok([
      { channel: 'push', sent: true, at: 1, words: 'Sent 18:42:06' },
      { channel: 'telegram', sent: false, at: null, words: 'Not linked' },
    ]),
    ...extra,
  })
}

beforeEach(() => {
  held = null
  permission = 'default'
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  manager = {
    getSubscription: vi.fn(async () => held),
    subscribe: vi.fn(async () => {
      held = {
        endpoint: 'https://push.example.invalid/one',
        toJSON: () => ({
          endpoint: 'https://push.example.invalid/one',
          keys: { p256dh: 'p', auth: 'a' },
        }),
        options: { applicationServerKey: new Uint8Array([1, 2, 3, 4, 5, 6]).buffer },
        unsubscribe: vi.fn(async () => {
          held = null
          return true
        }),
      }
      return held
    }),
  }
  ask = vi.fn(async () => {
    permission = 'granted'
    return 'granted' as NotificationPermission
  })
  resetRegistrationForTests()
  resetResyncForTests()
  resetInstallForTests()
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
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
})

afterEach(() => {
  cleanup()
  resetFamilyManagerForTests()
  vi.unstubAllGlobals()
  delete (navigator as unknown as { serviceWorker?: unknown }).serviceWorker
  window.history.replaceState({}, '', '/')
})

const onlyFamily = (urls: string[]) => urls.every((url) => url.startsWith('/family/api/'))

describe('Notifications on this phone', () => {
  it('is off, and asks Chrome nothing, until the tap', async () => {
    const router = service()
    mountFamily('/family/settings', <Notifications />)
    const toggle = await screen.findByRole('switch', { name: 'Notifications on this phone' })
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('false'))
    expect(toggle.textContent).toContain('Off')
    expect(ask).not.toHaveBeenCalled()
    expect(manager.subscribe).not.toHaveBeenCalled()
    await userEvent.click(toggle)
    await waitFor(() =>
      expect(
        screen
          .getByRole('switch', { name: 'Notifications on this phone' })
          .getAttribute('aria-checked')
      ).toBe('true')
    )
    expect(ask).toHaveBeenCalledTimes(1)
    expect(router.calls.find((call) => call.url === SUBSCRIBE)?.method).toBe('POST')
    expect(onlyFamily(router.urls())).toBe(true)
  })

  it('turns off by telling the service first', async () => {
    permission = 'granted'
    held = {
      endpoint: 'https://push.example.invalid/one',
      toJSON: () => ({ endpoint: 'x', keys: {} }),
      unsubscribe: vi.fn(async () => true),
    }
    const router = service()
    mountFamily('/family/settings', <Notifications />)
    const toggle = await screen.findByRole('switch', { name: 'Notifications on this phone' })
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'))
    held = { ...held, unsubscribe: held.unsubscribe }
    await userEvent.click(toggle)
    await waitFor(() =>
      expect(router.calls.some((call) => call.url.endsWith('/push/subscriptions/remove'))).toBe(
        true
      )
    )
    expect(router.calls.find((call) => call.url.endsWith('/remove'))?.body).toEqual({
      endpoint: 'https://push.example.invalid/one',
    })
  })

  // Verification N5 (v0.12.0): on a phone two members share, the switch of the member who did
  // not turn notifications on read On, from the browser's subscription alone. Fails on the old
  // card: aria-checked was true.
  it('reads Off for a member when another member turned notifications on here', async () => {
    permission = 'granted'
    held = {
      endpoint: 'https://push.example.invalid/one',
      toJSON: () => ({ endpoint: 'x', keys: {} }),
      unsubscribe: vi.fn(async () => true),
    }
    const me = meOf('viewer')
    window.localStorage.setItem('sd-family-push', `on:${me.member.id + 1}`)
    service()
    mountFamily(
      '/family/settings',
      <FamilyMeContext.Provider value={me}>
        <Notifications />
      </FamilyMeContext.Provider>
    )
    const toggle = await screen.findByRole('switch', { name: 'Notifications on this phone' })
    await waitFor(() => expect(toggle.textContent).toContain('Off'))
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    cleanup()
    window.localStorage.setItem('sd-family-push', `on:${me.member.id}`)
    mountFamily(
      '/family/settings',
      <FamilyMeContext.Provider value={me}>
        <Notifications />
      </FamilyMeContext.Provider>
    )
    const mine = await screen.findByRole('switch', { name: 'Notifications on this phone' })
    await waitFor(() => expect(mine.getAttribute('aria-checked')).toBe('true'))
  })

  it('says how to unblock when Chrome has them blocked, and cannot be switched on', async () => {
    permission = 'denied'
    service()
    mountFamily('/family/settings', <Notifications />)
    expect(
      await screen.findByText(
        "Notifications are blocked for this site in Chrome. Open Chrome's site settings to allow them."
      )
    ).toBeTruthy()
    const toggle = screen.getByRole('switch', {
      name: 'Notifications on this phone',
    }) as HTMLButtonElement
    expect(toggle.disabled).toBe(true)
    expect(ask).not.toHaveBeenCalled()
  })

  it('says when the phone cannot show notifications at all', async () => {
    vi.stubGlobal('PushManager', undefined)
    delete (window as unknown as { PushManager?: unknown }).PushManager
    service()
    mountFamily('/family/settings', <Notifications />)
    expect(
      await screen.findByText('This phone cannot show notifications from this app.')
    ).toBeTruthy()
    expect(screen.queryByRole('switch', { name: 'Notifications on this phone' })).toBeNull()
  })

  it('says "Notifications may not arrive on this phone" when the service marks the device failing', async () => {
    service({ '/family/api/devices': ok([device({ notifications: 'failing' })]) })
    mountFamily('/family/settings', <Notifications />)
    const sentences = await screen.findAllByText(
      'Notifications may not arrive on this phone. Turn them off and on again in Settings.'
    )
    expect(sentences.length).toBeGreaterThan(0)
  })

  it('does not say it for a device that is fine, or for another device that is failing', async () => {
    service({
      '/family/api/devices': ok([
        device(),
        device({ id: 'other', current: false, notifications: 'failing' }),
      ]),
    })
    mountFamily('/family/settings', <Notifications />)
    await screen.findByRole('switch', { name: 'Notifications on this phone' })
    await waitFor(() => expect(screen.queryByText(/may not arrive/)).toBeNull())
  })
})

describe('what the member chooses', () => {
  it('saves the daily summary by push with the settings version it read', async () => {
    const router = service()
    mountFamily('/family/settings', <Notifications />)
    await userEvent.click(await screen.findByRole('switch', { name: 'Push' }))
    await waitFor(() => expect(router.calls.some((call) => call.method === 'PUT')).toBe(true))
    expect(router.calls.find((call) => call.method === 'PUT')).toMatchObject({
      url: SETTINGS_URL,
      body: { version: 7, notify: { digest_push: true, digest_telegram: true } },
    })
    expect(router.calls.find((call) => call.method === 'PUT')?.headers['X-Family-CSRF']).toBe(
      'tok123'
    )
  })

  it('cannot switch Telegram on before it is linked, and says why', async () => {
    service({ [SETTINGS_URL]: ok({ ...SETTINGS, telegram_linked: false }) })
    mountFamily('/family/settings', <Notifications />)
    const toggle = (await screen.findByRole('switch', { name: 'Telegram' })) as HTMLButtonElement
    expect(toggle.disabled).toBe(true)
    expect(screen.getByText('Telegram is not linked. Link it below.')).toBeTruthy()
  })

  it('says sign-in notices are always on', async () => {
    service()
    mountFamily('/family/settings', <Notifications />)
    expect(await screen.findByText(/Always on\. Push and Telegram, never silenced\./)).toBeTruthy()
    expect(screen.queryByRole('switch', { name: /sign-in/i })).toBeNull()
  })

  it('edits quiet hours and keeps the other quiet settings as they were', async () => {
    const router = service()
    mountFamily('/family/settings', <Notifications />)
    const from = (await screen.findByLabelText('From')) as HTMLInputElement
    expect(from.value).toBe('21:00')
    expect((screen.getByLabelText('To') as HTMLInputElement).value).toBe('08:00')
    await userEvent.clear(from)
    await userEvent.type(from, '22:30')
    await userEvent.click(screen.getByRole('button', { name: 'Save quiet hours' }))
    await waitFor(() => expect(router.calls.some((call) => call.method === 'PUT')).toBe(true))
    expect(router.calls.find((call) => call.method === 'PUT')?.body).toEqual({
      version: 7,
      quiet: { from: '22:30', to: '08:00', digest: true, alerts: false },
    })
  })

  it('shows a refused save in the service words and then what the service holds', async () => {
    const router = service({
      [SETTINGS_URL]: (_url: string, init?: RequestInit) =>
        init?.method === 'PUT'
          ? fail('conflict', 'This was changed on another screen. Reload and try again.')
          : ok(SETTINGS),
    })
    mountFamily('/family/settings', <Notifications />)
    await userEvent.click(await screen.findByRole('switch', { name: 'Push' }))
    expect(
      await screen.findByText('This was changed on another screen. Reload and try again.')
    ).toBeTruthy()
    // read again after the refusal
    await waitFor(() =>
      expect(
        router.calls.filter((call) => call.url === SETTINGS_URL && call.method === 'GET').length
      ).toBeGreaterThan(1)
    )
  })
})

describe('Send a test', () => {
  it('says what happened on each channel, with an icon and the words', async () => {
    const router = service()
    mountFamily('/family/settings', <Notifications />)
    await userEvent.click(await screen.findByRole('button', { name: 'Send a test' }))
    const list = await screen.findByRole('list', { name: 'Test results' })
    const items = within(list).getAllByRole('listitem')
    expect(items[0].textContent).toBe('Push · Sent 18:42:06')
    expect(items[1].textContent).toBe('Telegram · Not linked')
    for (const item of items) expect(item.querySelector('svg')).not.toBeNull()
    expect(router.calls.find((call) => call.url.endsWith('/notify/test'))?.method).toBe('POST')
  })

  it('sends nothing until the tap', async () => {
    const router = service()
    mountFamily('/family/settings', <Notifications />)
    await screen.findByRole('button', { name: 'Send a test' })
    expect(router.urls().some((url) => url.includes('notify/test'))).toBe(false)
  })
})

describe('Settings carries it all', () => {
  it('shows the card and offers the install again on a tap', async () => {
    service()
    const prompted = vi.fn(async () => undefined)
    mountFamily('/family/settings', <FamilySettings />)
    expect(await screen.findByRole('switch', { name: 'Notifications on this phone' })).toBeTruthy()
    // Chrome has not offered one: the words say where the menu item is
    expect(screen.getByText('Chrome menu, Add to Home screen')).toBeTruthy()
    const event = new Event('beforeinstallprompt', { cancelable: true }) as Event & {
      prompt: () => Promise<void>
      userChoice: Promise<{ outcome: string }>
    }
    event.prompt = prompted
    event.userChoice = Promise.resolve({ outcome: 'accepted' })
    window.dispatchEvent(event)
    const button = await screen.findByRole('button', { name: 'Add सुदर्शन to your home screen' })
    expect(prompted).not.toHaveBeenCalled()
    await userEvent.click(button)
    expect(prompted).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByText('Chrome menu, Add to Home screen')).toBeNull())
  })
})

const INFO = {
  display_name: 'Invited Person',
  otpauth_uri: 'otpauth://totp/Synthetic:member?secret=JBSWY3DPEHPK3PXP',
  totp_key: 'JBSWY3DPEHPK3PXP',
  expires_at: Date.now() + 3_600_000,
  rules: { handle_min: 3, handle_max: 20, password_min: 12 },
}

async function joined(
  user: ReturnType<typeof userEvent.setup>,
  router: ReturnType<typeof routeFetch>
) {
  mountFamily('/family/join/abc123', <FamilyJoin />, '/family/join/:code')
  await user.click(await screen.findByRole('button', { name: 'Continue' }))
  await user.type(screen.getByLabelText('Sign-in name'), 'newmember')
  await user.type(screen.getByLabelText('Choose a password'), 'a sentence i will remember')
  await user.click(screen.getByRole('button', { name: 'Continue' }))
  await user.type(await screen.findByLabelText('Enter the 6-digit code it shows'), '123456')
  await user.click(screen.getByRole('button', { name: 'Finish' }))
  await screen.findByText('You are signed in')
  return router
}

describe('the end of joining', () => {
  const joinService = () =>
    service({
      '/family/api/join/abc123': ok(INFO),
      '/family/api/join': ok(meOf('viewer')),
      '/family/api/telegram/link': ok({ url: 'https://t.me/synthetic_bot?start=c', expires_at: 1 }),
    })

  it('offers the install on a tap only, then goes on to notifications', async () => {
    const user = userEvent.setup()
    const prompted = vi.fn(async () => undefined)
    const router = joinService()
    await joined(user, router)
    const event = new Event('beforeinstallprompt', { cancelable: true }) as Event & {
      prompt: () => Promise<void>
      userChoice: Promise<{ outcome: string }>
    }
    event.prompt = prompted
    event.userChoice = Promise.resolve({ outcome: 'dismissed' })
    window.dispatchEvent(event)
    const button = await screen.findByRole('button', { name: 'Add सुदर्शन to your home screen' })
    expect(prompted).not.toHaveBeenCalled()
    await user.click(button)
    expect(prompted).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Get the day’s summary and sign-in notices')).toBeTruthy()
  })

  it('asks Chrome for notifications only on Turn on, then offers Telegram', async () => {
    const user = userEvent.setup()
    const router = joinService()
    await joined(user, router)
    await user.click(screen.getByRole('button', { name: 'Later' }))
    expect(await screen.findByText('Get the day’s summary and sign-in notices')).toBeTruthy()
    expect(ask).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Turn on' }))
    expect(await screen.findByText('Link the family bot for the summary on Telegram')).toBeTruthy()
    expect(ask).toHaveBeenCalledTimes(1)
    expect(router.urls()).toContain(SUBSCRIBE)
  })

  it('says how to unblock when notifications are blocked, and Not now goes on', async () => {
    permission = 'denied'
    const user = userEvent.setup()
    const router = joinService()
    await joined(user, router)
    await user.click(screen.getByRole('button', { name: 'Later' }))
    await user.click(await screen.findByRole('button', { name: 'Turn on' }))
    expect(
      await screen.findByText(
        "Notifications are blocked for this site in Chrome. Open Chrome's site settings to allow them."
      )
    ).toBeTruthy()
    expect(ask).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Not now' }))
    expect(await screen.findByText('Link the family bot for the summary on Telegram')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Later' }))
    expect(where.path).toBe('/family/')
  })
})
