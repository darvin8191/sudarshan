// SUDARSHAN P6 D3 (M2.10, M20.4): the member's own settings (design 6.4): the devices signed in
// (this phone first, a name that can be changed, Sign out per device, Sign out everywhere),
// Telegram, language, and the password. What a member never changes here is what the account
// owner shares or hides: that is the owner's, through his own page.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, LogOut, Pencil, Smartphone } from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  INSTALL_FALLBACK,
  INSTALL_TITLE,
  promptInstall,
  useInstallState,
} from '@/sudarshan/pwa/install'
import { FamilyError, familyApi } from '../api'
import { getFamilyManager } from '../familyManager'
import { DANGER, Field, OUTLINE, PRIMARY, Problem, Saved, Section } from '../forms'
import { DEVICES_KEY, FAILING_WORDS, SETTINGS_KEY } from '../keys'
import { Notifications } from '../Notifications'
import { clearSignedIn, forgetMember } from '../session'
import type { Device, Language } from '../types'
import { LIMITS } from '../types'
import { deviceWhen } from '../when'

export const PASSWORD_LINE = 'Changing your password signs out your other devices.'
export const LANGUAGE_LINE = 'For Telegram and notifications; the app stays in English.'
export const ABOUT_LINE =
  'You see what the account owner shares. Hidden figures are hidden on purpose.'

const wordsOf = (error: unknown) =>
  error instanceof FamilyError ? error.message : 'That could not be sent. Try again.'

/** Everything this phone knew of the session is dropped, and the sign-in page is shown. */
function useLeave() {
  const client = useQueryClient()
  const navigate = useNavigate()
  return () => {
    clearSignedIn() // the member's own tap: the sign-in page has no reason to give
    getFamilyManager().disconnect()
    forgetMember(client)
    navigate('/family/login', { replace: true })
  }
}

/** What Sign out asks first, for this phone and for another device. */
export const signOutAsk = (current: boolean) =>
  current
    ? 'Sign out of this phone? You will need your password and code to come back.'
    : 'Sign that device out? It will need your password and code to come back.'

function DeviceRow({
  device,
  now,
  onChanged,
  onGone,
}: {
  device: Device
  now: number
  onChanged: () => void
  onGone: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState(device.label)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState(false)

  const save = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    try {
      await familyApi.deviceRename(device.id, label.trim())
      setEditing(false)
      onChanged()
    } catch (error) {
      setProblem(wordsOf(error))
    } finally {
      setBusy(false)
    }
  }

  const signOut = async () => {
    setBusy(true)
    setProblem(null)
    try {
      await familyApi.deviceSignout(device.id)
      if (device.current) onGone()
      else onChanged()
    } catch (error) {
      setProblem(wordsOf(error))
      setAsking(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li
      data-device={device.id}
      className="flex flex-col gap-2 border-t border-[color:var(--divider)] pt-3 first:border-t-0 first:pt-0"
    >
      {editing ? (
        <form onSubmit={save} className="flex flex-col gap-2">
          <Field
            label="Device name"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            maxLength={LIMITS.device_label_max}
            error={problem}
          />
          <div className="grid grid-cols-2 gap-2">
            <button type="submit" disabled={busy || label.trim() === ''} className={PRIMARY}>
              Save name
            </button>
            <button
              type="button"
              className={OUTLINE}
              onClick={() => {
                setEditing(false)
                setLabel(device.label)
                setProblem(null)
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <div className="flex items-start gap-2">
            <Smartphone
              aria-hidden="true"
              className="mt-0.5 size-5 shrink-0 text-[color:var(--text-secondary)]"
            />
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <p className="text-[0.9375rem] font-semibold break-words">
                {device.label}
                {device.current && (
                  <span className="ml-2 rounded-md bg-[color:var(--primary-tint)] px-1.5 py-0.5 text-xs font-bold text-primary">
                    This phone
                  </span>
                )}
              </p>
              <p className="text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
                {deviceWhen(device.created_at, device.last_seen_at, now)}
                {device.place ? ` · ${device.place}` : ''}
              </p>
              {device.notifications !== null && (
                <p className="text-[0.8125rem] font-semibold text-[color:var(--warning-text)]">
                  {FAILING_WORDS}
                </p>
              )}
            </div>
          </div>
          {problem && <Problem text={problem} />}
          <div className="grid grid-cols-2 gap-2">
            <button type="button" className={OUTLINE} onClick={() => setEditing(true)}>
              <Pencil aria-hidden="true" className="size-4" />
              Rename
            </button>
            <button
              type="button"
              disabled={busy}
              className={OUTLINE}
              aria-expanded={asking}
              onClick={() => setAsking(true)}
            >
              <LogOut aria-hidden="true" className="size-4" />
              Sign out
            </button>
          </div>
          {/* Review L1 (v0.12.0): one tap signed the device out; coming back takes a password
              and a code, so it asks, as Sign out everywhere does. */}
          {asking && (
            <div
              role="alertdialog"
              aria-label={signOutAsk(device.current)}
              className="flex flex-col gap-2 rounded-[0.875rem] bg-[color:var(--down-tint)] p-3"
            >
              <p className="text-sm font-semibold">{signOutAsk(device.current)}</p>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  disabled={busy}
                  className={DANGER}
                  onClick={() => void signOut()}
                >
                  Sign out
                </button>
                <button type="button" className={OUTLINE} onClick={() => setAsking(false)}>
                  Stay signed in
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </li>
  )
}

function Devices() {
  const client = useQueryClient()
  const leave = useLeave()
  const devices = useQuery({
    queryKey: DEVICES_KEY,
    queryFn: () => familyApi.devices(),
    retry: false,
    staleTime: 0,
  })
  const [asking, setAsking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const refresh = () => void client.invalidateQueries({ queryKey: DEVICES_KEY })
  const now = Date.now()

  const everywhere = async () => {
    setProblem(null)
    try {
      await familyApi.logoutAll()
      leave()
    } catch (error) {
      setProblem(wordsOf(error))
    }
  }

  const rows = [...(devices.data ?? [])].sort((a, b) => Number(b.current) - Number(a.current))
  return (
    <Section title="Devices">
      {devices.isPending && <p className="text-sm text-muted-foreground">Reading your devices.</p>}
      {devices.error && <Problem text={wordsOf(devices.error)} />}
      <ul aria-label="Devices" className="flex flex-col gap-3">
        {rows.map((device) => (
          <DeviceRow key={device.id} device={device} now={now} onChanged={refresh} onGone={leave} />
        ))}
      </ul>
      {problem && <Problem text={problem} />}
      {asking ? (
        <div className="flex flex-col gap-2 rounded-[0.875rem] bg-[color:var(--down-tint)] p-3">
          <p className="text-sm font-semibold">
            Sign out of every device, this one too? You will need your password and code to come
            back.
          </p>
          <div className="grid grid-cols-2 gap-2">
            <button type="button" className={DANGER} onClick={() => void everywhere()}>
              Sign out everywhere
            </button>
            <button type="button" className={OUTLINE} onClick={() => setAsking(false)}>
              Stay signed in
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className={DANGER} onClick={() => setAsking(true)}>
          <LogOut aria-hidden="true" className="size-4" />
          Sign out everywhere
        </button>
      )}
    </Section>
  )
}

/** "Add सुदर्शन to your home screen": offered again here, on a tap, for a phone that said Later. */
function InstallCard() {
  const state = useInstallState()
  if (state === 'installed') return null
  return (
    <Section title={INSTALL_TITLE}>
      {state === 'available' ? (
        <button type="button" className={PRIMARY} onClick={() => void promptInstall()}>
          <Smartphone aria-hidden="true" className="size-4" />
          {INSTALL_TITLE}
        </button>
      ) : (
        <p className="text-sm text-[color:var(--text-secondary)]">{INSTALL_FALLBACK}</p>
      )}
    </Section>
  )
}

function TelegramCard({ linked, onChanged }: { linked: boolean; onChanged: () => void }) {
  const [link, setLink] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const start = async () => {
    setBusy(true)
    setProblem(null)
    try {
      const made = await familyApi.telegramLink()
      setLink(made.url)
      window.open(made.url, '_blank', 'noopener')
    } catch (error) {
      setProblem(wordsOf(error))
    } finally {
      setBusy(false)
    }
  }
  const unlink = async () => {
    setBusy(true)
    setProblem(null)
    try {
      await familyApi.telegramUnlink()
      setLink(null)
      onChanged()
    } catch (error) {
      setProblem(wordsOf(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title="Telegram">
      <p className="text-sm text-[color:var(--text-secondary)]">
        {linked
          ? 'Linked to the family bot.'
          : 'Not linked. The family bot sends the day’s summary.'}
      </p>
      {link && (
        <a
          href={link}
          target="_blank"
          rel="noopener noreferrer"
          className={`${OUTLINE} no-underline`}
        >
          <ExternalLink aria-hidden="true" className="size-4" />
          Open Telegram
        </a>
      )}
      {problem && <Problem text={problem} />}
      <div className="grid grid-cols-1 gap-2">
        <button type="button" disabled={busy} className={PRIMARY} onClick={() => void start()}>
          Link Telegram
        </button>
        {linked && (
          <button type="button" disabled={busy} className={OUTLINE} onClick={() => void unlink()}>
            Unlink
          </button>
        )}
      </div>
    </Section>
  )
}

const LANGUAGES: Array<{ value: Language; label: string }> = [
  { value: 'en', label: 'English' },
  { value: 'gu', label: 'ગુજરાતી' },
]

function LanguageCard({
  language,
  version,
  onChanged,
}: {
  language: Language
  version: number
  onChanged: () => void
}) {
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const choose = async (next: Language) => {
    if (next === language) return
    setBusy(true)
    setProblem(null)
    try {
      await familyApi.settingsSet({ version, language: next })
    } catch (error) {
      setProblem(wordsOf(error))
    } finally {
      // Changed or refused, the screen shows what the service now holds.
      onChanged()
      setBusy(false)
    }
  }
  return (
    <Section title="Language">
      <fieldset className="flex gap-2">
        <legend className="sr-only">Language</legend>
        {LANGUAGES.map((each) => (
          <button
            key={each.value}
            type="button"
            aria-pressed={language === each.value}
            disabled={busy}
            onClick={() => void choose(each.value)}
            className={`${language === each.value ? PRIMARY : OUTLINE} flex-1`}
          >
            {each.label}
          </button>
        ))}
      </fieldset>
      <p className="text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
        {LANGUAGE_LINE}
      </p>
      {problem && <Problem text={problem} />}
    </Section>
  )
}

function PasswordCard() {
  const client = useQueryClient()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [totp, setTotp] = useState('')
  const [problem, setProblem] = useState<{ field: string | null; text: string } | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSaved(null)
    if (next.length < LIMITS.password_min) {
      return setProblem({
        field: 'password',
        text: `A password is at least ${LIMITS.password_min} characters. A sentence you will remember works best.`,
      })
    }
    setBusy(true)
    setProblem(null)
    try {
      const done = await familyApi.password({
        current,
        password: next,
        totp: totp.replace(/\s+/g, ''),
      })
      setSaved(done.words ?? 'Your password was changed. Your other devices are signed out.')
      setCurrent('')
      setNext('')
      setTotp('')
      // The other devices are gone from the list now.
      void client.invalidateQueries({ queryKey: DEVICES_KEY })
    } catch (error) {
      setProblem({
        field: error instanceof FamilyError ? error.field : null,
        text: wordsOf(error),
      })
      setTotp('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title="Password and code app">
      <form onSubmit={submit} noValidate className="flex flex-col gap-3">
        <Field
          label="Current password"
          type="password"
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
          autoComplete="current-password"
        />
        <Field
          label="New password"
          type="password"
          value={next}
          onChange={(event) => setNext(event.target.value)}
          autoComplete="new-password"
          maxLength={LIMITS.password_max}
          error={problem?.field === 'password' ? problem.text : null}
        />
        <Field
          label="6-digit code"
          value={totp}
          onChange={(event) => setTotp(event.target.value.replace(/[^\d\s]/g, ''))}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={7}
        />
        <p className="text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
          {PASSWORD_LINE}
        </p>
        {problem && problem.field !== 'password' && <Problem text={problem.text} />}
        {saved && <Saved text={saved} />}
        <button
          type="submit"
          disabled={busy || current === '' || next === '' || totp === ''}
          className={PRIMARY}
        >
          Change password
        </button>
      </form>
    </Section>
  )
}

export default function FamilySettings() {
  const client = useQueryClient()
  const settings = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: () => familyApi.settings(),
    retry: false,
    staleTime: 0,
  })
  const refresh = () => void client.invalidateQueries({ queryKey: SETTINGS_KEY })
  return (
    <div className="flex flex-col gap-3">
      <Devices />
      <Notifications />
      {settings.error && <Problem text={wordsOf(settings.error)} />}
      {settings.data && (
        <>
          <TelegramCard linked={settings.data.telegram_linked} onChanged={refresh} />
          <LanguageCard
            language={settings.data.language}
            version={settings.data.version}
            onChanged={refresh}
          />
        </>
      )}
      <PasswordCard />
      <InstallCard />
      <Section title="About this view">
        <p className="text-sm text-[color:var(--text-secondary)]">{ABOUT_LINE}</p>
      </Section>
    </div>
  )
}
