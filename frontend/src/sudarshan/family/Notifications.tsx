// SUDARSHAN P6 D8 (M20.4, M10.6): the Notifications card of the member's Settings (design 6.4):
// on or off for this phone, the daily summary by push and by Telegram, the sign-in notices that
// are always on, quiet hours, and a test send that says what happened on each channel.
//
// Nothing here asks Chrome for permission except the tap on "Turn on". A phone the service marks
// as failing says so in the design's words, under the toggle.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, X } from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import {
  BLOCKED_WORDS,
  disablePush,
  enablePush,
  type PushStatus,
  pushStatus,
  UNSUPPORTED_WORDS,
} from '@/sudarshan/pwa/push'
import { FamilyError, familyApi } from './api'
import { Field, OUTLINE, PRIMARY, Problem, Saved, Section } from './forms'
import { DEVICES_KEY, FAILING_WORDS, SETTINGS_KEY } from './keys'
import { useFamilyMeOptional } from './session'
import type { Channel, Notify, TestResult } from './types'

export const SIGNIN_NOTICES = 'Push and Telegram, never silenced'
const CHANNEL: Record<Channel, string> = { push: 'Push', telegram: 'Telegram' }
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/

const wordsOf = (error: unknown) =>
  error instanceof FamilyError ? error.message : 'That could not be sent. Try again.'

/** An on/off control that says which in words as well as by position. */
function Switch({
  label,
  checked,
  onChange,
  disabled = false,
  hint,
}: {
  label: string
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
  hint?: string
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className="flex min-h-12 w-full items-center justify-between gap-3 rounded-[0.875rem] border border-input bg-card px-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
      >
        <span className="text-[0.9375rem] font-semibold">{label}</span>
        <span
          aria-hidden="true"
          className={`inline-flex min-w-14 items-center justify-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold ${
            checked
              ? 'bg-[color:var(--primary-tint)] text-primary'
              : 'bg-[color:var(--surface-2)] text-[color:var(--text-secondary)]'
          }`}
        >
          {checked ? <Check className="size-3.5" /> : <X className="size-3.5" />}
          {checked ? 'On' : 'Off'}
        </span>
      </button>
      {hint && (
        <p className="px-1 text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
          {hint}
        </p>
      )}
    </div>
  )
}

export function Notifications() {
  const me = useFamilyMeOptional()
  const memberId = me?.member.id ?? null
  const client = useQueryClient()
  const settings = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: () => familyApi.settings(),
    retry: false,
    staleTime: 0,
  })
  const devices = useQuery({
    queryKey: DEVICES_KEY,
    queryFn: () => familyApi.devices(),
    retry: false,
    staleTime: 0,
  })
  const [status, setStatus] = useState<PushStatus | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [from, setFrom] = useState<string | null>(null)
  const [to, setTo] = useState<string | null>(null)
  const [results, setResults] = useState<TestResult[] | null>(null)

  useEffect(() => {
    let live = true
    void pushStatus(memberId).then((now) => live && setStatus(now))
    return () => {
      live = false
    }
  }, [memberId])

  const data = settings.data
  const failing = devices.data?.find((device) => device.current)?.notifications != null
  const refreshDevices = () => void client.invalidateQueries({ queryKey: DEVICES_KEY })
  const refreshSettings = () => void client.invalidateQueries({ queryKey: SETTINGS_KEY })

  const toggleDevice = async (on: boolean) => {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    try {
      if (on) setStatus(await enablePush(memberId))
      else {
        await disablePush()
        setStatus(await pushStatus(memberId))
      }
      refreshDevices()
    } catch (error) {
      setProblem(wordsOf(error))
      setStatus(await pushStatus(memberId))
    } finally {
      setBusy(false)
    }
  }

  const save = async (body: { notify?: Notify; quiet?: NonNullable<typeof data>['quiet'] }) => {
    if (!data) return
    setBusy(true)
    setProblem(null)
    setSaved(null)
    try {
      await familyApi.settingsSet({ version: data.version, ...body })
      setSaved('Saved.')
    } catch (error) {
      setProblem(wordsOf(error))
    } finally {
      // Saved or refused, the card then shows what the service holds.
      refreshSettings()
      setBusy(false)
    }
  }

  const saveQuiet = (event: FormEvent) => {
    event.preventDefault()
    if (!data) return
    const start = from ?? data.quiet.from
    const end = to ?? data.quiet.to
    if (!CLOCK.test(start) || !CLOCK.test(end)) {
      return setProblem('Quiet hours need a start and an end time, like 21:00 and 08:00.')
    }
    void save({ quiet: { ...data.quiet, from: start, to: end } })
  }

  const sendTest = async () => {
    setBusy(true)
    setProblem(null)
    setResults(null)
    try {
      setResults(await familyApi.testSend())
    } catch (error) {
      setProblem(wordsOf(error))
    } finally {
      setBusy(false)
    }
  }

  const deviceOn = status === 'on'
  return (
    <Section title="Notifications">
      {status === 'unsupported' ? (
        <p className="text-sm text-[color:var(--text-secondary)]">{UNSUPPORTED_WORDS}</p>
      ) : (
        <Switch
          label="Notifications on this phone"
          checked={deviceOn}
          disabled={busy || status === null || status === 'blocked'}
          onChange={(next) => void toggleDevice(next)}
          hint={
            status === 'blocked'
              ? BLOCKED_WORDS
              : failing
                ? FAILING_WORDS
                : 'The day’s summary and sign-in notices arrive here.'
          }
        />
      )}
      {failing && status !== 'blocked' && status !== 'unsupported' && (
        <Problem text={FAILING_WORDS} />
      )}

      {data && (
        <>
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-bold">Daily summary</h3>
            <Switch
              label="Push"
              checked={data.notify.digest_push}
              disabled={busy}
              onChange={(next) => void save({ notify: { ...data.notify, digest_push: next } })}
            />
            <Switch
              label="Telegram"
              checked={data.notify.digest_telegram}
              disabled={busy || !data.telegram_linked}
              hint={data.telegram_linked ? undefined : 'Telegram is not linked. Link it below.'}
              onChange={(next) => void save({ notify: { ...data.notify, digest_telegram: next } })}
            />
          </div>

          <div className="flex flex-col gap-0.5">
            <h3 className="text-sm font-bold">Sign-in notices</h3>
            <p className="text-sm text-[color:var(--text-secondary)]">
              Always on. {SIGNIN_NOTICES}.
            </p>
          </div>

          <form onSubmit={saveQuiet} noValidate className="flex flex-col gap-2">
            <h3 className="text-sm font-bold">Quiet hours</h3>
            <div className="grid grid-cols-2 gap-2">
              <Field
                label="From"
                type="time"
                value={from ?? data.quiet.from}
                onChange={(event) => setFrom(event.target.value)}
              />
              <Field
                label="To"
                type="time"
                value={to ?? data.quiet.to}
                onChange={(event) => setTo(event.target.value)}
              />
            </div>
            <button type="submit" disabled={busy} className={OUTLINE}>
              Save quiet hours
            </button>
          </form>
        </>
      )}

      {saved && <Saved text={saved} />}
      {problem && <Problem text={problem} />}
      {settings.error && <Problem text={wordsOf(settings.error)} />}

      <div className="flex flex-col gap-2">
        <button type="button" disabled={busy} className={PRIMARY} onClick={() => void sendTest()}>
          Send a test
        </button>
        {results && (
          <ul aria-label="Test results" className="flex flex-col gap-1">
            {results.map((each) => (
              <li
                key={each.channel}
                data-channel={each.channel}
                className="flex items-start gap-2 text-sm font-semibold"
              >
                {each.sent ? (
                  <Check
                    aria-hidden="true"
                    className="mt-0.5 size-4 shrink-0 text-[color:var(--up)]"
                  />
                ) : (
                  <X
                    aria-hidden="true"
                    className="mt-0.5 size-4 shrink-0 text-[color:var(--down)]"
                  />
                )}
                <span>
                  {CHANNEL[each.channel]} · {each.words}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  )
}
