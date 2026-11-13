// SUDARSHAN P6 D3 (M2.9): joining the family view from the owner's invite link
// (`/family/join/:code`), in the design's own words (6.3). One public page, one step at a time:
// the welcome, a sign-in name and a password, the code app, and then what follows a sign-in on
// this phone (the install hint, the Telegram link). The account owner carries the link himself;
// nothing here sends it anywhere.
//
// The password and the code are only ever sent to the service in the one `join` call. Neither is
// kept in the address, in storage or in a log. A wrong code keeps the other answers on screen.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ExternalLink, Smartphone } from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { BrandLockup } from '@/sudarshan/BrandLockup'
import { FamilyError, familyApi } from '../api'
import { FamilyBadge } from '../FamilyBadge'
import { Field, OUTLINE, PRIMARY, Problem } from '../forms'
import { QrCode } from '../QrCode'
import { ME_KEY } from '../session'
import { LIMITS, type Me } from '../types'

export const WELCOME = 'You are invited to the family view of सुदर्शन · SUDARSHAN'
export const WELCOME_LINE =
  "This view shows the account owner's positions and lists. It cannot place or change orders."
export const HANDLE_HINT = 'Short, letters and digits (for example a first name)'
export const PASSWORD_HINT = 'At least 12 characters. A sentence you will remember works best.'
export const CODE_APP_LINE = 'Scan with Google Authenticator or any code app, or type the key'
export const STAY_LINE = 'This phone stays signed in for 30 days. Sign out any time in Settings.'
export const INSTALL_TITLE = 'Add सुदर्शन to your home screen'
export const INSTALL_FALLBACK = 'Chrome menu, Add to Home screen'
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
      // The session is made: the shell reads it from here instead of asking again.
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
      if (failure.code === 'invite_gone') setStep('welcome')
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

type After = 'install' | 'telegram'

/**
 * What follows a sign-in on this phone, one step at a time and each one skippable: the sentence
 * that it stays signed in for 30 days, the install hint, the Telegram link. It ends on `/family`.
 */
function AfterJoin() {
  const navigate = useNavigate()
  const [step, setStep] = useState<After>('install')
  const [link, setLink] = useState<{ url: string } | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const land = () => navigate('/family', { replace: true })

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
          <p className="text-sm text-[color:var(--text-secondary)]">{INSTALL_FALLBACK}</p>
          <button type="button" className={`${OUTLINE} w-full`} onClick={() => setStep('telegram')}>
            Later
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
