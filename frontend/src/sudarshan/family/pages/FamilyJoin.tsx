// SUDARSHAN P6 D3 (M2.9): joining the family view from the owner's invite link
// (`/family/join/:code`), in the design's own words (6.3). One public page, one step at a time:
// the welcome, a sign-in name and a password, the code app, and then what follows a sign-in on
// this phone (the install hint, the Telegram link). The account owner carries the link himself;
// nothing here sends it anywhere.
//
// The password and the code are only ever sent to the service in the one `join` call. Neither is
// kept in the address, in storage or in a log. A wrong code keeps the other answers on screen.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, Check, Copy, ExternalLink, Smartphone } from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { BrandLockup } from '@/sudarshan/BrandLockup'
import {
  INSTALL_FALLBACK,
  INSTALL_TITLE,
  promptInstall,
  useInstallState,
} from '@/sudarshan/pwa/install'
import { BLOCKED_WORDS, enablePush, type PushStatus } from '@/sudarshan/pwa/push'
import { registerFamilyWorker } from '@/sudarshan/pwa/register'
import { FamilyError, familyApi } from '../api'
import { FamilyBadge } from '../FamilyBadge'
import { Field, OUTLINE, PRIMARY, Problem } from '../forms'
import { FAMILY_HOME } from '../navigation'
import { QrCode } from '../QrCode'
import { forgetMember, ME_KEY } from '../session'
import { LIMITS, type Me } from '../types'

export const WELCOME = 'You are invited to the family view of सुदर्शन · SUDARSHAN'
export const WELCOME_LINE =
  "This view shows the account owner's positions and lists. It cannot place or change orders."
export const HANDLE_HINT = 'Short, letters and digits (for example a first name)'
export const PASSWORD_HINT = 'At least 12 characters. A sentence you will remember works best.'
export const CODE_APP_LINE = 'Scan with Google Authenticator or any code app, or type the key'
export const STAY_LINE = 'This phone stays signed in for 30 days. Sign out any time in Settings.'
export const NOTIFY_TITLE = 'Get the day’s summary and sign-in notices'
export const TELEGRAM_TITLE = 'Link the family bot for the summary on Telegram'

type Strength = 'Weak' | 'Fine' | 'Strong'

/** A label, never colour alone: short is Weak, long is Strong, in between is Fine. */
export function strengthOf(password: string): Strength | null {
  if (password === '') return null
  if (password.length < LIMITS.password_min) return 'Weak'
  return password.length >= 16 ? 'Strong' : 'Fine'
}

const HANDLE_RULE = /^[a-z0-9_]+$/

/** What a sign-in name must be, checked on the phone before the service checks it again. */
export function handleProblem(handle: string, min: number, max: number): string | null {
  if (handle.length < min || handle.length > max || !HANDLE_RULE.test(handle)) {
    return `A sign-in name is ${min} to ${max} small letters, digits or underscores.`
  }
  return null
}

/** The key as the code apps show it: groups of four, so it can be typed from the screen. */
export function keyInGroups(key: string): string {
  return key
    .replace(/\s+/g, '')
    .replace(/(.{4})/g, '$1 ')
    .trim()
}

export const KEY_COPIED = 'Key copied. Paste it into your code app as a new account.'
export const KEY_NOT_COPIED = 'This phone did not allow copying. Type the key into your code app.'

type Step = 'welcome' | 'sign-in' | 'code' | 'after'

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-6">
      <section className="flex w-full max-w-sm flex-col items-start gap-4 rounded-[1.125rem] border bg-card px-5 py-6 shadow-[var(--shadow-card)]">
        <BrandLockup variant="stacked" align="start" />
        <FamilyBadge />
        {children}
      </section>
    </div>
  )
}

export default function FamilyJoin() {
  const { code = '' } = useParams()
  const client = useQueryClient()
  const info = useQuery({
    queryKey: ['family', 'join', code],
    queryFn: () => familyApi.joinInfo(code),
    retry: false,
    // The invite carries a secret: it is read once and never kept for a later visit.
    gcTime: 0,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
  })
  const [step, setStep] = useState<Step>('welcome')
  const [handle, setHandle] = useState('')
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [problem, setProblem] = useState<{ field: string | null; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  // Review M5 (v0.12.0): the invite ended while the member was joining (it ran out, or was used
  // or replaced meanwhile). The service's sentence, shown on the invite-ended screen.
  const [gone, setGone] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)

  // The family worker is what lets Chrome offer the install at the end of joining; the shell that
  // normally registers it is not mounted on this public page.
  useEffect(() => {
    void registerFamilyWorker()
  }, [])

  if (info.isPending) {
    return (
      <Frame>
        <output className="block text-sm text-[color:var(--text-secondary)]">
          Opening your invite.
        </output>
      </Frame>
    )
  }
  if (!info.data) {
    const text = info.error instanceof FamilyError ? info.error.message : null
    return (
      <Frame>
        <h1 className="sd-display text-[1.5rem] font-bold">Invite</h1>
        <Problem
          text={
            text ?? 'This invite link could not be opened. Ask the account owner for a new one.'
          }
        />
        {info.error instanceof FamilyError && info.error.code === 'unanswered' && (
          <button type="button" className={PRIMARY} onClick={() => void info.refetch()}>
            Try again
          </button>
        )}
      </Frame>
    )
  }
  if (gone !== null) {
    // Nothing leads on from here: Continue would only go round to the same refusal.
    return (
      <Frame>
        <h1 className="sd-display text-[1.5rem] font-bold">Invite</h1>
        <Problem text={gone} />
      </Frame>
    )
  }
  const invite = info.data
  const { handle_min, handle_max, password_min } = invite.rules

  const toSignIn = (event: FormEvent) => {
    event.preventDefault()
    const name = handleProblem(handle, handle_min, handle_max)
    if (name) return setProblem({ field: 'handle', text: name })
    if (password.length < password_min) {
      return setProblem({
        field: 'password',
        text: `A password is at least ${password_min} characters. A sentence you will remember works best.`,
      })
    }
    setProblem(null)
    setStep('code')
  }

  const copyKey = async () => {
    try {
      await navigator.clipboard.writeText(invite.totp_key.replace(/\s+/g, ''))
      setCopied(KEY_COPIED)
    } catch {
      setCopied(KEY_NOT_COPIED)
    }
  }

  const finish = async (event: FormEvent) => {
    event.preventDefault()
    if (!/^\d{6}$/.test(totp.replace(/\s+/g, ''))) {
      return setProblem({ field: 'totp', text: 'Enter the 6 digits the code app shows.' })
    }
    setBusy(true)
    setProblem(null)
    try {
      const me: Me = await familyApi.join({
        code,
        handle,
        password,
        totp: totp.replace(/\s+/g, ''),
      })
      // The session is made: the shell reads it from here instead of asking again, and nothing
      // the page held for another member before shows for this one (review M5).
      forgetMember(client)
      client.setQueryData(ME_KEY, me)
      setPassword('')
      setTotp('')
      setStep('after')
    } catch (error) {
      const failure =
        error instanceof FamilyError
          ? error
          : new FamilyError('unanswered', 'That could not be sent. Try again.')
      setProblem({ field: failure.field ?? failure.code, text: failure.message })
      // A name or password the service refused goes back to where it is typed; the wrong code
      // stays here so the next one can be tried.
      if (failure.field === 'handle' || failure.field === 'password') setStep('sign-in')
      if (failure.code === 'invite_gone') {
        // what was typed for an invite that no longer exists is not kept
        setPassword('')
        setTotp('')
        setGone(failure.message)
      }
    } finally {
      setBusy(false)
    }
  }

  if (step === 'welcome') {
    return (
      <Frame>
        <h1 className="sd-display text-[1.5rem] leading-tight font-bold">{WELCOME}</h1>
        <p className="text-sm font-medium text-[color:var(--text-secondary)]">
          Invite for {invite.display_name}
        </p>
        <p className="text-sm text-[color:var(--text-secondary)]">{WELCOME_LINE}</p>
        <button type="button" className={`${PRIMARY} w-full`} onClick={() => setStep('sign-in')}>
          Continue
        </button>
      </Frame>
    )
  }

  if (step === 'sign-in') {
    const strength = strengthOf(password)
    return (
      <Frame>
        <form onSubmit={toSignIn} className="flex w-full flex-col gap-4" noValidate>
          <h1 className="sd-display text-[1.5rem] font-bold">Choose a sign-in name</h1>
          <Field
            label="Sign-in name"
            hint={HANDLE_HINT}
            value={handle}
            onChange={(event) => setHandle(event.target.value.toLowerCase())}
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            maxLength={handle_max}
            error={problem?.field === 'handle' ? problem.text : null}
          />
          <Field
            label="Choose a password"
            hint={PASSWORD_HINT}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="new-password"
            maxLength={LIMITS.password_max}
            error={problem?.field === 'password' ? problem.text : null}
          />
          {strength && (
            <p data-testid="strength" className="flex items-center gap-1.5 text-sm font-semibold">
              <Check aria-hidden="true" className="size-4" />
              Password strength: {strength}
            </p>
          )}
          <button type="submit" className={`${PRIMARY} w-full`}>
            Continue
          </button>
        </form>
      </Frame>
    )
  }

  if (step === 'code') {
    return (
      <Frame>
        <form onSubmit={finish} className="flex w-full flex-col gap-4" noValidate>
          <h1 className="sd-display text-[1.5rem] font-bold">Set up the code app</h1>
          <p className="text-sm text-[color:var(--text-secondary)]">{CODE_APP_LINE}</p>
          <QrCode value={invite.otpauth_uri} label="Code app set-up picture" />
          <p className="flex flex-col gap-0.5 text-sm">
            <span className="font-semibold text-[color:var(--text-secondary)]">Key</span>
            <code
              data-testid="totp-key"
              className="rounded-lg bg-[color:var(--bg-canvas)] px-3 py-2 text-base font-semibold break-all"
            >
              {keyInGroups(invite.totp_key)}
            </code>
          </p>
          {/* Review L2 (v0.12.0): a member joining on one phone cannot scan their own screen. */}
          <div className="grid w-full grid-cols-2 gap-2">
            <button type="button" className={OUTLINE} onClick={() => void copyKey()}>
              <Copy aria-hidden="true" className="size-4" />
              Copy key
            </button>
            <a href={invite.otpauth_uri} className={`${OUTLINE} no-underline`}>
              <ExternalLink aria-hidden="true" className="size-4" />
              Open in code app
            </a>
          </div>
          {copied && (
            <output className="block text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
              {copied}
            </output>
          )}
          <Field
            label="Enter the 6-digit code it shows"
            value={totp}
            onChange={(event) => setTotp(event.target.value.replace(/[^\d\s]/g, ''))}
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={7}
            error={problem?.text && problem.field !== 'handle' ? problem.text : null}
          />
          <button type="submit" disabled={busy} className={`${PRIMARY} w-full`}>
            Finish
          </button>
          <button
            type="button"
            className={`${OUTLINE} w-full`}
            onClick={() => {
              setProblem(null)
              setStep('sign-in')
            }}
          >
            Back
          </button>
        </form>
      </Frame>
    )
  }

  return <AfterJoin />
}

type After = 'install' | 'notifications' | 'telegram'

/**
 * What follows a sign-in on this phone, one step at a time and each one skippable: the sentence
 * that it stays signed in for 30 days, the install hint, the Telegram link. It ends on `/family/`.
 */
function AfterJoin() {
  const navigate = useNavigate()
  const [step, setStep] = useState<After>('install')
  const [link, setLink] = useState<{ url: string } | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const install = useInstallState()
  const [pushState, setPushState] = useState<PushStatus | null>(null)
  const land = () => navigate(FAMILY_HOME, { replace: true })

  // Chrome's permission question is asked here, on this tap, and nowhere before it.
  const turnOn = async () => {
    setBusy(true)
    setProblem(null)
    try {
      const now = await enablePush()
      setPushState(now)
      if (now === 'on') setStep('telegram')
    } catch (error) {
      setProblem(error instanceof FamilyError ? error.message : 'That could not be sent.')
    } finally {
      setBusy(false)
    }
  }

  const openTelegram = async () => {
    setBusy(true)
    setProblem(null)
    try {
      const made = await familyApi.telegramLink()
      setLink({ url: made.url })
      // Opened from the tap, in a new tab; the visible link below is for a phone that refused.
      window.open(made.url, '_blank', 'noopener')
    } catch (error) {
      setProblem(error instanceof FamilyError ? error.message : 'That could not be sent.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Frame>
      <h1 className="sd-display text-[1.5rem] font-bold">You are signed in</h1>
      <p className="text-sm text-[color:var(--text-secondary)]">{STAY_LINE}</p>
      {step === 'install' && (
        <section aria-label="Install" className="flex w-full flex-col gap-3">
          <h2 className="flex items-center gap-2 text-base font-bold">
            <Smartphone aria-hidden="true" className="size-5" />
            {INSTALL_TITLE}
          </h2>
          {install === 'available' ? (
            <button
              type="button"
              className={`${PRIMARY} w-full`}
              onClick={() => void promptInstall().then(() => setStep('notifications'))}
            >
              {INSTALL_TITLE}
            </button>
          ) : (
            <p className="text-sm text-[color:var(--text-secondary)]">
              {install === 'installed' ? 'Already on your home screen.' : INSTALL_FALLBACK}
            </p>
          )}
          <button
            type="button"
            className={`${OUTLINE} w-full`}
            onClick={() => setStep('notifications')}
          >
            Later
          </button>
        </section>
      )}
      {step === 'notifications' && (
        <section aria-label="Notifications" className="flex w-full flex-col gap-3">
          <h2 className="flex items-center gap-2 text-base font-bold">
            <Bell aria-hidden="true" className="size-5" />
            {NOTIFY_TITLE}
          </h2>
          {pushState === 'blocked' && <Problem text={BLOCKED_WORDS} />}
          {problem && <Problem text={problem} />}
          <button
            type="button"
            disabled={busy}
            className={`${PRIMARY} w-full`}
            onClick={() => void turnOn()}
          >
            Turn on
          </button>
          <button type="button" className={`${OUTLINE} w-full`} onClick={() => setStep('telegram')}>
            {pushState === 'on' ? 'Continue' : 'Not now'}
          </button>
        </section>
      )}
      {step === 'telegram' && (
        <section aria-label="Telegram" className="flex w-full flex-col gap-3">
          <h2 className="text-base font-bold">{TELEGRAM_TITLE}</h2>
          {link ? (
            <a
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              className={`${PRIMARY} w-full no-underline`}
            >
              <ExternalLink aria-hidden="true" className="size-4" />
              Open Telegram
            </a>
          ) : (
            <button
              type="button"
              disabled={busy}
              className={`${PRIMARY} w-full`}
              onClick={() => void openTelegram()}
            >
              <ExternalLink aria-hidden="true" className="size-4" />
              Open Telegram
            </button>
          )}
          {problem && <Problem text={problem} />}
          <button type="button" className={`${OUTLINE} w-full`} onClick={land}>
            {link ? 'Continue' : 'Later'}
          </button>
        </section>
      )}
    </Frame>
  )
}
