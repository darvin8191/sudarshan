// SUDARSHAN (M2.20): Upstox auto-login settings. Server: blueprints/sudarshan_autologin.py,
// worker: sudarshan/autologin/. The TOTP key and PIN are write-only here: the page can save or
// remove them and shows when they were saved, never their values. Neither field is a password
// field and there is no <form>, so no password manager offers to keep them: they are masked by
// CSS instead (MASKED), and Save is an ordinary button.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Ban,
  CalendarClock,
  CircleCheck,
  CircleMinus,
  CircleX,
  Eye,
  EyeOff,
  Info,
  KeyRound,
  Loader2,
  Lock,
  type LucideIcon,
  PlayCircle,
  TriangleAlert,
} from 'lucide-react'
import { type KeyboardEvent, type ReactNode, useState } from 'react'
import {
  type AutoLoginAttempt,
  type AutoLoginStatus,
  autologinApi,
  type RunOutcome,
  type SavedSecret,
  type TestResult,
} from '@/api/autologin'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { showToast } from '@/utils/toast'

export const STATUS_KEY = ['sudarshan-autologin-status'] as const
const TOTP_SHAPE = /^[A-Z2-7]{16,64}$/
const PIN_SHAPE = /^[0-9]{6}$/
/** Dots instead of characters, without type="password" (which invites a password manager). */
const MASKED = '[-webkit-text-security:disc]'
/** Attributes that keep password managers (and the keyboard's suggestions) away. */
const NOT_A_PASSWORD = {
  autoComplete: 'off',
  autoCorrect: 'off',
  autoCapitalize: 'off',
  spellCheck: false,
  'data-1p-ignore': true,
  'data-lpignore': 'true',
  'data-bwignore': true,
  'data-form-type': 'other',
} as const
// Brand tokens (sudarshan/theme.css), never a fixed palette: they follow colour-blind mode.
const UP = 'text-[color:var(--up)]'
const DOWN = 'text-[color:var(--down)]'
const WARNING = 'text-[color:var(--warning-text)]'

/**
 * The key as the server stores it: no spaces, upper case, no trailing padding.
 *
 * Upstox shows TOTP setup as a QR code, and a phone camera copies that code's text
 * (`otpauth://totp/...?secret=...`), so the whole text is accepted too and the key is taken out
 * of it, exactly as the server does. Only a standard code (SHA1, 6 digits, 30 s) qualifies;
 * anything else gives '' (not a key).
 */
export function normalizeTotpKey(raw: string): string {
  const text = raw.trim()
  if (/^otpauth:/i.test(text)) return keyFromQrText(text)
  return raw.replace(/\s+/g, '').toUpperCase().replace(/=+$/, '')
}

function keyFromQrText(text: string): string {
  let link: URL
  try {
    link = new URL(text)
  } catch {
    return ''
  }
  if (link.protocol.toLowerCase() !== 'otpauth:' || link.hostname.toLowerCase() !== 'totp') {
    return ''
  }
  const params = new Map<string, string>()
  link.searchParams.forEach((value, key) => params.set(key.toLowerCase(), value))
  if ((params.get('algorithm') ?? 'SHA1').toUpperCase() !== 'SHA1') return ''
  if ((params.get('digits') ?? '6') !== '6') return ''
  if ((params.get('period') ?? '30') !== '30') return ''
  const secret = params.get('secret') ?? ''
  return secret.replace(/\s+/g, '').toUpperCase().replace(/=+$/, '')
}

const shortTime = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const dayTime = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

function formatTime(iso: string | null, format = shortTime): string {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : `${format.format(date)} IST`
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message
  return fallback
}

const OUTCOMES: Record<RunOutcome | 'started', { label: string; icon: LucideIcon; tone: string }> =
  {
    ok: { label: 'Logged in', icon: CircleCheck, tone: UP },
    failed: { label: 'Failed', icon: CircleX, tone: DOWN },
    skipped: { label: 'Nothing to do', icon: CircleMinus, tone: 'text-muted-foreground' },
    refused: { label: 'Not run', icon: Ban, tone: WARNING },
    started: { label: 'Did not finish', icon: TriangleAlert, tone: WARNING },
  }

function Outcome({ outcome }: { outcome: RunOutcome | 'started' }) {
  const { label, icon: Icon, tone } = OUTCOMES[outcome] ?? OUTCOMES.failed
  return (
    <span className={`inline-flex items-center gap-1.5 font-medium ${tone}`}>
      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
      {label}
    </span>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-2 sm:flex-row sm:gap-4">
      <dt className="text-sm text-muted-foreground sm:w-40 sm:shrink-0">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  )
}

function SavedLine({
  what,
  secret,
  onRemove,
  removing,
}: {
  what: string
  secret: SavedSecret
  onRemove: () => void
  removing: boolean
}) {
  if (!secret.set) {
    return (
      <p className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
        <CircleMinus className="h-4 w-4" aria-hidden="true" />
        {what}: not saved
      </p>
    )
  }
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      {secret.readable ? (
        <p className={`inline-flex items-center gap-1.5 text-sm ${UP}`}>
          <Lock className="h-4 w-4" aria-hidden="true" />
          {what}: saved {formatTime(secret.updated_at)}
        </p>
      ) : (
        <p className={`inline-flex items-center gap-1.5 text-sm ${DOWN}`}>
          <TriangleAlert className="h-4 w-4" aria-hidden="true" />
          {what}: saved, but it can no longer be read. Save it again.
        </p>
      )}
      <Button
        type="button"
        variant="ghost"
        className="h-12"
        onClick={onRemove}
        disabled={removing}
        aria-label={`Remove the saved ${what}`}
      >
        Remove
      </Button>
    </div>
  )
}

function nextRunText(status: AutoLoginStatus): string {
  if (!status.enabled) return 'None while auto-login is off'
  if (status.paused) return 'None while paused'
  return formatTime(status.next_run, dayTime) || 'No trading day in the next two weeks'
}

function RecentAttempts({ items }: { items: AutoLoginAttempt[] }) {
  if (items.length === 0) return null
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">Recent attempts</h3>
      <ul className="divide-y rounded-md border">
        {items.map((item) => (
          <li key={`${item.at}-${item.kind}`} className="space-y-1 p-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>
                {formatTime(item.at)} {item.kind === 'test' ? '(Test now)' : '(scheduled)'}
              </span>
              <Outcome outcome={item.outcome} />
            </div>
            <p className="text-muted-foreground">{item.message}</p>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function AutoLogin() {
  const queryClient = useQueryClient()
  const status = useQuery({
    queryKey: STATUS_KEY,
    queryFn: autologinApi.status,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.running) return 3_000
      return data && !data.test.allowed ? 15_000 : 60_000
    },
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: STATUS_KEY })

  const [totpKey, setTotpKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [pin, setPin] = useState('')
  const [testResult, setTestResult] = useState<TestResult | null>(null)

  const normalizedKey = normalizeTotpKey(totpKey)
  const keyProblem = totpKey && !TOTP_SHAPE.test(normalizedKey)
  const pinProblem = pin && !PIN_SHAPE.test(pin)
  const canSave = Boolean((totpKey || pin) && !keyProblem && !pinProblem)

  const save = useMutation({
    mutationFn: async () => {
      const saved: string[] = []
      if (totpKey) {
        await autologinApi.saveTotpSecret(normalizedKey)
        setTotpKey('')
        setShowKey(false)
        saved.push('TOTP key')
      }
      if (pin) {
        await autologinApi.savePin(pin)
        setPin('')
        saved.push('PIN')
      }
      return saved
    },
    onSuccess: (saved) => showToast.success(`${saved.join(' and ')} saved.`),
    onError: (error) => showToast.error(errorMessage(error, 'Could not save. Try again.')),
    onSettled: refresh,
  })
  const remove = useMutation({
    mutationFn: (what: 'totp' | 'pin') =>
      what === 'totp' ? autologinApi.clearTotpSecret() : autologinApi.clearPin(),
    onSuccess: (message) => showToast.success(message || 'Removed.'),
    onError: (error) => showToast.error(errorMessage(error, 'Could not remove. Try again.')),
    onSettled: refresh,
  })
  const toggle = useMutation({
    mutationFn: autologinApi.setEnabled,
    onSuccess: (message) => showToast.success(message || 'Saved.'),
    onError: (error) => showToast.error(errorMessage(error, 'Could not change the switch.')),
    onSettled: refresh,
  })
  const test = useMutation({
    mutationFn: autologinApi.testNow,
    onMutate: () => setTestResult(null),
    onSuccess: (result) => {
      setTestResult(result)
      if (result.outcome === 'ok') showToast.success('Upstox auto-login test passed.')
      else showToast.error(result.message)
    },
    onError: (error) => showToast.error(errorMessage(error, 'The test could not run.')),
    onSettled: refresh,
  })

  const onSave = () => {
    if (canSave && !save.isPending) save.mutate()
  }
  const saveOnEnter = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') onSave()
  }

  if (status.isPending) {
    return (
      <output className="flex items-center justify-center py-16">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" aria-hidden="true" />
        <span className="sr-only">Loading auto-login settings</span>
      </output>
    )
  }
  if (status.isError || !status.data) {
    return (
      <div className="space-y-4 py-6">
        <Alert variant="destructive">
          <TriangleAlert className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Could not load the auto-login settings</AlertTitle>
          <AlertDescription>
            {errorMessage(status.error, 'Try again in a moment.')}
          </AlertDescription>
        </Alert>
        <Button className="h-12" onClick={() => status.refetch()}>
          Try again
        </Button>
      </div>
    )
  }

  const data = status.data
  const testBlocked = data.running || !data.test.allowed || test.isPending

  return (
    <div className="space-y-6 py-6">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <KeyRound className="h-6 w-6" aria-hidden="true" />
            Auto login
          </h1>
          <Badge variant={data.enabled ? 'default' : 'outline'}>
            {data.enabled ? <CircleCheck aria-hidden="true" /> : <CircleMinus aria-hidden="true" />}
            {data.enabled ? 'On' : 'Off'}
          </Badge>
        </div>
        <p className="text-muted-foreground">
          Logs in to Upstox for you on trading mornings with your TOTP key and PIN, so the day
          starts without the browser login.
        </p>
      </div>

      {!data.broker_supported && (
        <Alert variant="destructive">
          <TriangleAlert className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Upstox only</AlertTitle>
          <AlertDescription>
            OpenAlgo is set up for another broker. Auto-login works only with Upstox.
          </AlertDescription>
        </Alert>
      )}
      {data.paused && (
        <Alert variant="warning">
          <TriangleAlert className="h-4 w-4" aria-hidden="true" />
          <AlertTitle>Auto-login is paused</AlertTitle>
          <AlertDescription>
            <p>{data.paused.message}</p>
            <p>
              To resume, fix the cause, then save the TOTP key or the PIN again. Switching
              auto-login off and on does not resume it, so the same key and PIN are not sent to
              Upstox again. Test now waits for the new key or PIN too.
            </p>
          </AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CalendarClock className="h-5 w-5" aria-hidden="true" />
              Status
            </CardTitle>
            <CardDescription>
              Runs at {data.schedule.join(', ')} IST on trading days until one login works. Weekends
              and NSE holidays are skipped.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <dl className="divide-y">
              <Row label="Upstox today">
                {data.connected_today ? (
                  <span className={`inline-flex items-center gap-1.5 ${UP}`}>
                    <CircleCheck className="h-4 w-4" aria-hidden="true" />
                    Connected for today
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                    <CircleMinus className="h-4 w-4" aria-hidden="true" />
                    Not connected yet today
                  </span>
                )}
              </Row>
              <Row label="Last run">
                {data.last_run ? (
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Outcome outcome={data.last_run.outcome} />
                      <span className="text-muted-foreground">
                        {formatTime(data.last_run.at)}{' '}
                        {data.last_run.kind === 'test' ? '(Test now)' : '(scheduled)'}
                      </span>
                    </div>
                    <p>{data.last_run.message}</p>
                  </div>
                ) : (
                  'Not run yet'
                )}
              </Row>
              <Row label="Last good login">{formatTime(data.last_success_at) || 'None yet'}</Row>
              <Row label="Next run">{nextRunText(data)}</Row>
            </dl>

            {data.running && (
              <output className="inline-flex items-center gap-2 text-sm">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />A login attempt is
                running.
              </output>
            )}

            <div className="space-y-2">
              <Button
                type="button"
                className="h-12 w-full sm:w-auto"
                onClick={() => test.mutate()}
                disabled={testBlocked}
              >
                {test.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <PlayCircle className="h-4 w-4" aria-hidden="true" />
                )}
                {test.isPending ? 'Testing...' : 'Test now'}
              </Button>
              <p className="text-sm text-muted-foreground">
                One login now, at any time. Test now replaces today's login, so it is not available
                from 09:00 to 15:40 on a trading day while Upstox is connected, or while auto-login
                is paused.
              </p>
              {!data.test.allowed && data.test.reason && !test.isPending && (
                <p className={`inline-flex items-start gap-1.5 text-sm ${WARNING}`}>
                  <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  {data.test.reason}
                </p>
              )}
              {testResult && (
                <div className="space-y-1 rounded-md border p-3 text-sm" aria-live="polite">
                  <Outcome outcome={testResult.outcome} />
                  <p>{testResult.message}</p>
                </div>
              )}
            </div>

            <RecentAttempts items={data.recent} />
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Schedule</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between gap-4">
                <div className="space-y-1">
                  <Label htmlFor="autologin-enabled">Log in automatically on trading days</Label>
                  <p className="text-sm text-muted-foreground">
                    Off: nothing runs and nothing else changes. You can still log in by hand.
                  </p>
                </div>
                {/* 48 dp to touch: the after: layer widens the hit area, not the look. */}
                <Switch
                  id="autologin-enabled"
                  className="relative h-7 w-12 after:absolute after:-inset-3 after:content-['']"
                  checked={data.enabled}
                  disabled={toggle.isPending}
                  onCheckedChange={(checked) => toggle.mutate(checked)}
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Lock className="h-5 w-5" aria-hidden="true" />
                Upstox TOTP key and PIN
              </CardTitle>
              <CardDescription>
                Stored encrypted on your server and never shown again. You can replace or remove
                them.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-2">
                <SavedLine
                  what="TOTP key"
                  secret={data.secrets.totp_secret}
                  onRemove={() => remove.mutate('totp')}
                  removing={remove.isPending}
                />
                <SavedLine
                  what="PIN"
                  secret={data.secrets.pin}
                  onRemove={() => remove.mutate('pin')}
                  removing={remove.isPending}
                />
              </div>

              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="autologin-totp-key">
                    {data.secrets.totp_secret.set ? 'New TOTP key' : 'TOTP key'}
                  </Label>
                  <div className="relative">
                    <Input
                      id="autologin-totp-key"
                      type="text"
                      className={`h-12 pr-14 text-base ${showKey ? '' : MASKED}`}
                      placeholder="Paste the key, or the QR code's text"
                      value={totpKey}
                      onChange={(event) => setTotpKey(event.target.value)}
                      onKeyDown={saveOnEnter}
                      {...NOT_A_PASSWORD}
                      aria-invalid={Boolean(keyProblem)}
                      aria-describedby="autologin-totp-help"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      className="absolute top-0 right-0 h-12 w-12"
                      onClick={() => setShowKey((shown) => !shown)}
                      aria-label={showKey ? 'Hide TOTP key' : 'Show TOTP key'}
                    >
                      {showKey ? (
                        <EyeOff className="h-4 w-4" aria-hidden="true" />
                      ) : (
                        <Eye className="h-4 w-4" aria-hidden="true" />
                      )}
                    </Button>
                  </div>
                  <p
                    id="autologin-totp-help"
                    className={`text-sm ${keyProblem ? DOWN : 'text-muted-foreground'}`}
                    aria-live="polite"
                  >
                    {keyProblem
                      ? 'This is not a TOTP key. Paste the key (16 to 64 letters A to Z and digits 2 to 7; spaces are fine) or the whole text of the Upstox QR code.'
                      : 'The key (letters A to Z and digits 2 to 7), or the whole text a phone camera copies from the Upstox QR code.'}
                  </p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="autologin-pin">{data.secrets.pin.set ? 'New PIN' : 'PIN'}</Label>
                  <Input
                    id="autologin-pin"
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    maxLength={6}
                    className={`h-12 text-base tracking-widest ${MASKED}`}
                    placeholder="6 digits"
                    value={pin}
                    onChange={(event) =>
                      setPin(event.target.value.replace(/[^0-9]/g, '').slice(0, 6))
                    }
                    onKeyDown={saveOnEnter}
                    {...NOT_A_PASSWORD}
                    aria-invalid={Boolean(pinProblem)}
                    aria-describedby="autologin-pin-help"
                  />
                  <p
                    id="autologin-pin-help"
                    className={`text-sm ${pinProblem ? DOWN : 'text-muted-foreground'}`}
                  >
                    {pinProblem
                      ? 'The PIN has exactly 6 digits.'
                      : 'Your 6-digit Upstox login PIN.'}
                  </p>
                </div>

                <Button
                  type="button"
                  className="h-12 w-full sm:w-auto"
                  onClick={onSave}
                  disabled={!canSave || save.isPending}
                >
                  {save.isPending && (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  )}
                  Save
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Info className="h-5 w-5" aria-hidden="true" />
                Where is the TOTP key?
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <ol className="list-decimal space-y-2 pl-5">
                <li>
                  On a laptop open account.upstox.com &gt; Profile &gt; Time-based OTP (TOTP) &gt;
                  Enable TOTP, and enter the SMS OTP. A QR code appears. (In the Upstox app: Upstox
                  icon &gt; My account &gt; Profile &gt; Time-based OTP (TOTP).) If TOTP is already
                  on, reset it there to get a new QR code.
                </li>
                <li>
                  Scan that QR code with your phone's authenticator app, such as Google
                  Authenticator. Upstox still lets you log in with the SMS OTP too.
                </li>
                <li>
                  For this page: if Upstox shows a key under the QR code, paste the key above. If
                  not, point the phone camera at the same QR code, copy the text it shows (it starts
                  with otpauth://) and paste the whole text above. Do not keep that text anywhere
                  else.
                </li>
                <li>
                  Finish on Upstox: type the 6-digit code from the authenticator app and press
                  Enable TOTP.
                </li>
                <li>Enter your 6-digit Upstox PIN, Save, then press Test now once.</li>
              </ol>
              <p lang="gu">
                Laptop પર account.upstox.com &gt; Profile &gt; Time-based OTP (TOTP) &gt; Enable
                TOTP ખોલો અને SMS OTP નાખો; QR code દેખાશે. એ QR ને phone ના authenticator app (Google
                Authenticator) થી scan કરો. QR ની નીચે key દેખાય તો એ key અહીં paste કરો; ન દેખાય તો
                phone ના camera થી એ જ QR scan કરી, દેખાતું લખાણ (otpauth:// થી શરૂ થતું) આખું copy કરી અહીં
                paste કરો. પછી Upstox પર authenticator નો 6 આંકડાનો code નાખી Enable TOTP દબાવો. છેલ્લે
                તમારો 6 આંકડાનો Upstox PIN નાખી Save અને એક વાર Test now.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
