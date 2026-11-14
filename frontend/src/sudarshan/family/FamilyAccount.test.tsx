// SUDARSHAN P6 D3: joining, signing in and the member's settings, through the real pages with a
// fake service. Synthetic names and keys only. Every request a page makes must go to /family/api.
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetFamilyManagerForTests } from './familyManager'
import FamilyJoin, { keyInGroups, strengthOf } from './pages/FamilyJoin'
import FamilyLogin from './pages/FamilyLogin'
import FamilySettings from './pages/FamilySettings'
import { FakeSocket, fail, meOf, ok, routeFetch } from './testing/fixtures'
import { mountFamily, stubCookies, where } from './testing/harness'

const INFO = {
  display_name: 'Invited Person',
  otpauth_uri: 'otpauth://totp/Synthetic:member?secret=JBSWY3DPEHPK3PXP&issuer=Synthetic',
  totp_key: 'JBSWY3DPEHPK3PXP',
  expires_at: Date.now() + 3_600_000,
  rules: { handle_min: 3, handle_max: 20, password_min: 12 },
}
const GOOD_PASSWORD = 'a sentence i will remember'

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  stubCookies('__Host-sudarshan_family_csrf=tok123')
  where.path = ''
})

afterEach(() => {
  cleanup()
  resetFamilyManagerForTests()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const onlyFamily = (urls: string[]) => urls.every((url) => url.startsWith('/family/api/'))

async function toCodeStep(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Continue' }))
  await user.type(screen.getByLabelText('Sign-in name'), 'newmember')
  await user.type(screen.getByLabelText('Choose a password'), GOOD_PASSWORD)
  await user.click(screen.getByRole('button', { name: 'Continue' }))
  await screen.findByText('Set up the code app')
}

describe('Join', () => {
  it('opens with the design words and the invite name, and asks the service for the invite only', async () => {
    const router = routeFetch({ '/family/api/join/abc123': ok(INFO) })
    mountFamily('/family/join/abc123', <FamilyJoin />, '/family/join/:code')
    expect(
      await screen.findByText('You are invited to the family view of सुदर्शन · SUDARSHAN')
    ).toBeTruthy()
    expect(
      screen.getByText(
        "This view shows the account owner's positions and lists. It cannot place or change orders."
      )
    ).toBeTruthy()
    expect(screen.getByText(/Invited Person/)).toBeTruthy()
    expect(screen.getByTestId('family-badge')).toBeTruthy()
    expect(router.urls()).toEqual(['/family/api/join/abc123'])
  })

  it('says what is wrong with a link that is spent, in the service words, with no form', async () => {
    routeFetch(
      {
        '/family/api/join/old': fail(
          'invite_gone',
          'This invite link has expired or was already used. Ask the account owner for a new one.'
        ),
      },
      { '/family/api/join/old': 410 }
    )
    mountFamily('/family/join/old', <FamilyJoin />, '/family/join/:code')
    expect(await screen.findByText(/has expired or was already used/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Continue' })).toBeNull()
    expect(screen.queryByText(/410|invite_gone/)).toBeNull()
  })

  it('checks the name and password before it goes on, and labels the strength in words', async () => {
    routeFetch({ '/family/api/join/abc123': ok(INFO) })
    const user = userEvent.setup()
    mountFamily('/family/join/abc123', <FamilyJoin />, '/family/join/:code')
    await user.click(await screen.findByRole('button', { name: 'Continue' }))
    expect(screen.getByText('Short, letters and digits (for example a first name)')).toBeTruthy()
    expect(
      screen.getByText('At least 12 characters. A sentence you will remember works best.')
    ).toBeTruthy()
    await user.type(screen.getByLabelText('Sign-in name'), 'Ab')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByText(/A sign-in name is 3 to 20 small letters/)).toBeTruthy()
    // the input lowercases what is typed
    expect((screen.getByLabelText('Sign-in name') as HTMLInputElement).value).toBe('ab')
    await user.type(screen.getByLabelText('Choose a password'), 'short')
    expect(screen.getByTestId('strength').textContent).toContain('Weak')
    await user.type(screen.getByLabelText('Choose a password'), ' enough')
    expect(screen.getByTestId('strength').textContent).toContain('Fine')
    await user.type(screen.getByLabelText('Choose a password'), ' and then some more')
    expect(screen.getByTestId('strength').textContent).toContain('Strong')
  })

  it('rates a password by its length', () => {
    expect(strengthOf('')).toBeNull()
    expect(strengthOf('x'.repeat(11))).toBe('Weak')
    expect(strengthOf('x'.repeat(12))).toBe('Fine')
    expect(strengthOf('x'.repeat(15))).toBe('Fine')
    expect(strengthOf('x'.repeat(16))).toBe('Strong')
    expect(keyInGroups('JBSWY3DPEHPK3PXP')).toBe('JBSW Y3DP EHPK 3PXP')
  })

  it('shows the QR and the key to type, and keeps the secret out of the address', async () => {
    routeFetch({ '/family/api/join/abc123': ok(INFO) })
    const user = userEvent.setup()
    mountFamily('/family/join/abc123', <FamilyJoin />, '/family/join/:code')
    await toCodeStep(user)
    expect(
      screen.getByText(/Scan with Google Authenticator or any code app, or type the key/)
    ).toBeTruthy()
    const qr = screen.getByRole('img', { name: 'Code app set-up picture' })
    expect(qr.querySelectorAll('path').length).toBeGreaterThan(0)
    expect(screen.getByTestId('totp-key').textContent).toBe('JBSW Y3DP EHPK 3PXP')
    expect(screen.getByLabelText('Enter the 6-digit code it shows')).toBeTruthy()
    expect(window.location.href).not.toContain('JBSW')
  })

  it('keeps the answers after a wrong code, says the service sentence, and signs in on the next', async () => {
    let tries = 0
    const router = routeFetch({
      '/family/api/join/abc123': ok(INFO),
      '/family/api/join': (_url: string, init?: RequestInit) => {
        if (init?.method !== 'POST') return ok(INFO)
        tries += 1
        return tries === 1
          ? fail(
              'code_mismatch',
              'That code did not match. Codes change every 30 seconds; try the next one.',
              'totp'
            )
          : ok(meOf('viewer'))
      },
    })
    const user = userEvent.setup()
    const { client } = mountFamily('/family/join/abc123', <FamilyJoin />, '/family/join/:code')
    await toCodeStep(user)
    await user.type(screen.getByLabelText('Enter the 6-digit code it shows'), '123456')
    await user.click(screen.getByRole('button', { name: 'Finish' }))
    expect(
      await screen.findByText(
        'That code did not match. Codes change every 30 seconds; try the next one.'
      )
    ).toBeTruthy()
    expect(screen.getByText('Set up the code app')).toBeTruthy()
    await user.clear(screen.getByLabelText('Enter the 6-digit code it shows'))
    await user.type(screen.getByLabelText('Enter the 6-digit code it shows'), '654321')
    await user.click(screen.getByRole('button', { name: 'Finish' }))
    expect(await screen.findByText('You are signed in')).toBeTruthy()
    const posts = router.calls.filter((call) => call.method === 'POST')
    expect(posts).toHaveLength(2)
    expect(posts[1].body).toEqual({
      code: 'abc123',
      handle: 'newmember',
      password: GOOD_PASSWORD,
      totp: '654321',
    })
    expect(posts[1].headers['X-Family-CSRF']).toBe('tok123')
    // the password is not in any address
    expect(router.urls().some((url) => url.includes('remember'))).toBe(false)
    expect(client.getQueryData(['family', 'me'])).toBeTruthy()
    expect(onlyFamily(router.urls())).toBe(true)
  })

  it('sends a refused name back to where it is typed', async () => {
    routeFetch({
      '/family/api/join/abc123': ok(INFO),
      '/family/api/join': (_url: string, init?: RequestInit) =>
        init?.method === 'POST'
          ? fail('invalid', 'That sign-in name is taken. Choose another.', 'handle')
          : ok(INFO),
    })
    const user = userEvent.setup()
    mountFamily('/family/join/abc123', <FamilyJoin />, '/family/join/:code')
    await toCodeStep(user)
    await user.type(screen.getByLabelText('Enter the 6-digit code it shows'), '123456')
    await user.click(screen.getByRole('button', { name: 'Finish' }))
    expect(await screen.findByText('That sign-in name is taken. Choose another.')).toBeTruthy()
    expect(screen.getByLabelText('Sign-in name')).toBeTruthy()
  })

  it('ends with the stay-signed-in sentence, the install hint and the Telegram link, each skippable', async () => {
    const opened = vi.spyOn(window, 'open').mockImplementation(() => null)
    const router = routeFetch({
      '/family/api/join/abc123': ok(INFO),
      '/family/api/join': ok(meOf('viewer')),
      '/family/api/telegram/link': ok({
        url: 'https://t.me/synthetic_bot?start=code1',
        expires_at: 1,
      }),
    })
    const user = userEvent.setup()
    mountFamily('/family/join/abc123', <FamilyJoin />, '/family/join/:code')
    await toCodeStep(user)
    await user.type(screen.getByLabelText('Enter the 6-digit code it shows'), '123456')
    await user.click(screen.getByRole('button', { name: 'Finish' }))
    expect(
      await screen.findByText(
        'This phone stays signed in for 30 days. Sign out any time in Settings.'
      )
    ).toBeTruthy()
    expect(screen.getByText('Add सुदर्शन to your home screen')).toBeTruthy()
    expect(screen.getByText('Chrome menu, Add to Home screen')).toBeTruthy()
    // nothing is asked of Telegram until a tap
    expect(router.urls().some((url) => url.includes('telegram'))).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Later' }))
    // the notification step sits between them and asks nothing until a tap
    await user.click(await screen.findByRole('button', { name: 'Not now' }))
    expect(screen.getByText('Link the family bot for the summary on Telegram')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Open Telegram' }))
    await waitFor(() =>
      expect(opened).toHaveBeenCalledWith(
        'https://t.me/synthetic_bot?start=code1',
        '_blank',
        'noopener'
      )
    )
    const link = await screen.findByRole('link', { name: 'Open Telegram' })
    expect(link.getAttribute('href')).toBe('https://t.me/synthetic_bot?start=code1')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(where.path).toBe('/family')
    expect(onlyFamily(router.urls())).toBe(true)
  })
})

describe('Login', () => {
  it('shows the service sentence as it came, the same for a wrong name and a wrong password', async () => {
    const sentence = 'That sign-in name, password or code did not match.'
    routeFetch(
      {
        '/family/api/me': fail('signed_out', 'You were signed out on this device.'),
        '/family/api/login': fail('signin_failed', sentence),
      },
      { '/family/api/me': 401, '/family/api/login': 401 }
    )
    const user = userEvent.setup()
    mountFamily('/family/login', <FamilyLogin />)
    for (const name of ['nobody', 'member_one']) {
      await user.clear(screen.getByLabelText('Sign-in name'))
      await user.type(screen.getByLabelText('Sign-in name'), name)
      await user.clear(screen.getByLabelText('Password'))
      await user.type(screen.getByLabelText('Password'), 'wrong wrong wrong')
      await user.type(screen.getByLabelText('6-digit code'), '000000')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))
      const alert = await screen.findByRole('alert')
      expect(alert.textContent).toBe(sentence)
    }
    expect(
      screen.getByText(
        /Ask the account owner to send a new invite; it replaces your password and code app/
      )
    ).toBeTruthy()
  })

  it('shows the lock-out sentence with its time', async () => {
    routeFetch(
      {
        '/family/api/me': fail('signed_out', 'x'),
        '/family/api/login': fail('locked', 'Too many wrong attempts. Try again at 19:05.'),
      },
      { '/family/api/me': 401, '/family/api/login': 429 }
    )
    const user = userEvent.setup()
    mountFamily('/family/login', <FamilyLogin />)
    await user.type(screen.getByLabelText('Sign-in name'), 'member_one')
    await user.type(screen.getByLabelText('Password'), 'whatever it is')
    await user.type(screen.getByLabelText('6-digit code'), '123456')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Too many wrong attempts. Try again at 19:05.')).toBeTruthy()
    expect(screen.queryByText(/429/)).toBeNull()
  })

  it('needs all three answers, sends them with the page token, and lands on /family', async () => {
    const router = routeFetch(
      {
        '/family/api/me': fail('signed_out', 'x'),
        '/family/api/login': ok(meOf('viewer')),
      },
      { '/family/api/me': 401 }
    )
    const user = userEvent.setup()
    mountFamily('/family/login', <FamilyLogin />)
    const submit = screen.getByRole('button', { name: 'Sign in' }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    await user.type(screen.getByLabelText('Sign-in name'), 'Member_One')
    await user.type(screen.getByLabelText('Password'), 'a long enough password')
    expect(submit.disabled).toBe(true)
    await user.type(screen.getByLabelText('6-digit code'), '123 456')
    expect(submit.disabled).toBe(false)
    await user.click(submit)
    await waitFor(() => expect(where.path).toBe('/family'))
    const post = router.calls.find((call) => call.method === 'POST')
    expect(post?.body).toEqual({
      handle: 'member_one',
      password: 'a long enough password',
      totp: '123456',
    })
    expect(post?.headers['X-Family-CSRF']).toBe('tok123')
    expect(onlyFamily(router.urls())).toBe(true)
  })

  it('goes straight on when the phone is already signed in', async () => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mountFamily('/family/login', <FamilyLogin />)
    await waitFor(() => expect(where.path).toBe('/family'))
  })
})

const NOW = Date.UTC(2026, 10, 14, 6, 0, 0)

function devicesOf() {
  return [
    {
      id: 'dev-other',
      label: 'Second phone',
      created_at: Date.UTC(2026, 10, 2, 6, 0, 0),
      last_seen_at: NOW - 3_600_000,
      place: null,
      current: false,
      notifications: 'failing',
    },
    {
      id: 'dev-this',
      label: 'Android 15 · Chrome 130',
      created_at: Date.UTC(2026, 10, 2, 6, 0, 0),
      last_seen_at: NOW,
      place: null,
      current: true,
      notifications: null,
    },
  ]
}

const SETTINGS = {
  language: 'en',
  quiet: { from: '21:00', to: '08:00', digest: true, alerts: true },
  notify: { digest_push: false, digest_telegram: false },
  telegram_linked: false,
  version: 4,
}

describe('Settings', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  function service(extra: Record<string, unknown> = {}) {
    return routeFetch({
      '/family/api/devices': (_url: string, init?: RequestInit) =>
        init?.method && init.method !== 'GET' ? ok({ ok: true, words: null }) : ok(devicesOf()),
      '/family/api/settings': (_url: string, init?: RequestInit) =>
        init?.method === 'PUT' ? ok({ ok: true, words: null }) : ok(SETTINGS),
      '/family/api/password': ok({
        ok: true,
        words: 'Your password was changed. Your other devices are signed out.',
      }),
      '/family/api/logout_all': ok({ ok: true, words: 'You were signed out on every device.' }),
      '/family/api/telegram/link': ok({ url: 'https://t.me/synthetic_bot?start=c', expires_at: 1 }),
      '/family/api/telegram/unlink': ok({ ok: true, words: null }),
      ...extra,
    })
  }

  it('lists this phone first with its dates, and says where notifications may not arrive', async () => {
    const router = service()
    mountFamily('/family/settings', <FamilySettings />)
    const items = await screen.findAllByRole('listitem')
    const devices = items.filter((item) => item.hasAttribute('data-device'))
    expect(devices.map((item) => item.getAttribute('data-device'))).toEqual([
      'dev-this',
      'dev-other',
    ])
    expect(devices[0].textContent).toContain('This phone')
    expect(devices[0].textContent).toContain('Signed in 2 Nov · last used today')
    expect(devices[1].textContent).toContain(
      'Notifications may not arrive on this phone. Turn them off and on again in Settings.'
    )
    expect(devices[0].textContent).not.toContain('Notifications may not arrive')
    expect(
      screen.getByText(
        'You see what the account owner shares. Hidden figures are hidden on purpose.'
      )
    ).toBeTruthy()
    expect(onlyFamily(router.urls())).toBe(true)
  })

  it('renames a device and signs another one out, with the page token', async () => {
    const router = service()
    const user = userEvent.setup()
    mountFamily('/family/settings', <FamilySettings />)
    const other = (await screen.findAllByRole('listitem')).find(
      (item) => item.getAttribute('data-device') === 'dev-other'
    ) as HTMLElement
    await user.click(within(other).getByRole('button', { name: /Rename/ }))
    await user.clear(screen.getByLabelText('Device name'))
    await user.type(screen.getByLabelText('Device name'), 'Tablet')
    await user.click(screen.getByRole('button', { name: 'Save name' }))
    await waitFor(() => expect(router.calls.some((call) => call.method === 'PATCH')).toBe(true))
    const patch = router.calls.find((call) => call.method === 'PATCH')
    expect(patch?.url).toBe('/family/api/devices/dev-other')
    expect(patch?.body).toEqual({ label: 'Tablet' })
    expect(patch?.headers['X-Family-CSRF']).toBe('tok123')
    const again = (await screen.findAllByRole('listitem')).find(
      (item) => item.getAttribute('data-device') === 'dev-other'
    ) as HTMLElement
    await user.click(within(again).getByRole('button', { name: /Sign out/ }))
    await waitFor(() => expect(router.calls.some((call) => call.method === 'DELETE')).toBe(true))
    expect(router.calls.find((call) => call.method === 'DELETE')?.url).toBe(
      '/family/api/devices/dev-other'
    )
    expect(where.path).toBe('/family/settings')
  })

  it('signing this phone out leaves for the sign-in page', async () => {
    service()
    const user = userEvent.setup()
    mountFamily('/family/settings', <FamilySettings />, '/family/settings')
    const mine = (await screen.findAllByRole('listitem')).find(
      (item) => item.getAttribute('data-device') === 'dev-this'
    ) as HTMLElement
    // review M1: a sign-out by the member's own tap leaves the sign-in page no reason to give
    window.localStorage.setItem('sd-family-was-in', '1')
    await user.click(within(mine).getByRole('button', { name: /Sign out/ }))
    await waitFor(() => expect(where.path).toBe('/family/login'))
    expect(window.localStorage.getItem('sd-family-was-in')).toBeNull()
  })

  it('asks before Sign out everywhere, then leaves', async () => {
    const router = service()
    const user = userEvent.setup()
    mountFamily('/family/settings', <FamilySettings />)
    await screen.findAllByRole('listitem')
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere' }))
    expect(router.calls.some((call) => call.url.includes('logout_all'))).toBe(false)
    expect(screen.getByText(/Sign out of every device, this one too/)).toBeTruthy()
    await user.click(screen.getAllByRole('button', { name: 'Sign out everywhere' })[0])
    await waitFor(() => expect(where.path).toBe('/family/login'))
    expect(router.calls.some((call) => call.url.includes('logout_all'))).toBe(true)
  })

  it('Link Telegram opens the link the service gave, and offers it to tap', async () => {
    const opened = vi.spyOn(window, 'open').mockImplementation(() => null)
    service()
    const user = userEvent.setup()
    mountFamily('/family/settings', <FamilySettings />)
    await user.click(await screen.findByRole('button', { name: 'Link Telegram' }))
    await waitFor(() =>
      expect(opened).toHaveBeenCalledWith(
        'https://t.me/synthetic_bot?start=c',
        '_blank',
        'noopener'
      )
    )
    expect((await screen.findByRole('link', { name: 'Open Telegram' })).getAttribute('href')).toBe(
      'https://t.me/synthetic_bot?start=c'
    )
    expect(screen.queryByRole('button', { name: 'Unlink' })).toBeNull()
  })

  it('offers Unlink only when linked', async () => {
    service({
      '/family/api/settings': ok({ ...SETTINGS, telegram_linked: true }),
    })
    const user = userEvent.setup()
    const router = service({ '/family/api/settings': ok({ ...SETTINGS, telegram_linked: true }) })
    mountFamily('/family/settings', <FamilySettings />)
    await user.click(await screen.findByRole('button', { name: 'Unlink' }))
    await waitFor(() =>
      expect(router.urls().some((url) => url.includes('telegram/unlink'))).toBe(true)
    )
  })

  it('changes the language with the settings version it read', async () => {
    const router = service()
    const user = userEvent.setup()
    mountFamily('/family/settings', <FamilySettings />)
    await user.click(await screen.findByRole('button', { name: 'ગુજરાતી' }))
    await waitFor(() => expect(router.calls.some((call) => call.method === 'PUT')).toBe(true))
    expect(router.calls.find((call) => call.method === 'PUT')?.body).toEqual({
      version: 4,
      language: 'gu',
    })
    expect(
      screen.getByText('For Telegram and notifications; the app stays in English.')
    ).toBeTruthy()
  })

  it('changes the password, says it signs out the others, and shows the service words', async () => {
    const router = service()
    const user = userEvent.setup()
    mountFamily('/family/settings', <FamilySettings />)
    await screen.findAllByRole('listitem')
    expect(screen.getByText('Changing your password signs out your other devices.')).toBeTruthy()
    await user.type(screen.getByLabelText('Current password'), 'the old one is long')
    await user.type(screen.getByLabelText('New password'), 'short')
    await user.type(screen.getAllByLabelText('6-digit code')[0], '123456')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(screen.getByText(/A password is at least 12 characters/)).toBeTruthy()
    expect(router.calls.some((call) => call.url.endsWith('/password'))).toBe(false)
    await user.clear(screen.getByLabelText('New password'))
    await user.type(screen.getByLabelText('New password'), 'the new one is longer')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(
      await screen.findByText('Your password was changed. Your other devices are signed out.')
    ).toBeTruthy()
    const post = router.calls.find((call) => call.url.endsWith('/password'))
    expect(post?.body).toEqual({
      current: 'the old one is long',
      password: 'the new one is longer',
      totp: '123456',
    })
    expect((screen.getByLabelText('New password') as HTMLInputElement).value).toBe('')
    expect(onlyFamily(router.urls())).toBe(true)
  })

  it('shows a refused password change in the service words', async () => {
    service({
      '/family/api/password': fail('invalid', 'Your current password or the code did not match.'),
    })
    const user = userEvent.setup()
    mountFamily('/family/settings', <FamilySettings />)
    await screen.findAllByRole('listitem')
    await user.type(screen.getByLabelText('Current password'), 'not the right one')
    await user.type(screen.getByLabelText('New password'), 'the new one is longer')
    await user.type(screen.getAllByLabelText('6-digit code')[0], '123456')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('Your current password or the code did not match.')).toBeTruthy()
  })
})
