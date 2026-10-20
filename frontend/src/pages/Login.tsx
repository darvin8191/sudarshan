import { ArrowLeft, ArrowRight, Eye, EyeOff, Fingerprint, Loader2, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { BrandLockup } from '@/sudarshan/BrandLockup'
import { showToast } from '@/utils/toast'

// SUDARSHAN: the D login. The brand's own colours rather than the mode's
// accent, because who is signing in has not chosen Live or Analyze yet.
const FIELD =
  'h-[3.25rem] rounded-xl bg-card px-3.5 text-[1.0625rem] font-medium shadow-none md:text-[1.0625rem] focus-visible:border-[color:var(--brand)] disabled:opacity-60'
const LABEL = 'text-[0.84375rem] font-semibold text-muted-foreground'
const PRIMARY =
  'inline-flex h-14 items-center justify-center gap-2.5 rounded-[0.875rem] bg-[color:var(--brand)] px-5 text-white transition-colors outline-none hover:bg-[color:var(--brand-hover)] focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-60 sd-display text-[1.4375rem] font-bold tracking-[0.04em]'

/** The faint rings behind the lockup: the icon's ring, echoed. Decoration only. */
function Rings() {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 420 420"
      className="pointer-events-none absolute -right-40 -top-36 size-[26.25rem]"
    >
      <circle
        cx="210"
        cy="210"
        r="196"
        fill="none"
        stroke="var(--gold)"
        strokeOpacity="0.28"
        strokeWidth="1.5"
      />
      <circle
        cx="210"
        cy="210"
        r="160"
        fill="none"
        stroke="var(--brand)"
        strokeOpacity="0.11"
        strokeWidth="2"
      />
      <circle
        cx="210"
        cy="210"
        r="124"
        fill="none"
        stroke="var(--brand)"
        strokeOpacity="0.07"
        strokeWidth="1.5"
      />
    </svg>
  )
}

export default function Login() {
  const navigate = useNavigate()
  const { login: setLogin } = useAuthStore()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [step, setStep] = useState<'password' | 'totp'>('password')
  const [showPassword, setShowPassword] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [isCheckingSetup, setIsCheckingSetup] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Check if setup is required or already logged in on page load
  useEffect(() => {
    const checkSetup = async () => {
      try {
        // First check if setup is needed
        const setupResponse = await fetch('/auth/check-setup', {
          credentials: 'include',
        })
        const setupData = await setupResponse.json()
        if (setupData.needs_setup) {
          navigate('/setup', { replace: true })
          return
        }

        // Check if already logged in
        const sessionResponse = await fetch('/auth/session-status', {
          credentials: 'include',
        })

        // Only process if response is successful (not 401 etc.)
        if (sessionResponse.ok) {
          const sessionData = await sessionResponse.json()

          if (sessionData.status === 'success' && sessionData.logged_in && sessionData.broker) {
            // Already fully logged in with broker, go to dashboard
            navigate('/dashboard', { replace: true })
            return
          } else if (
            sessionData.status === 'success' &&
            sessionData.authenticated &&
            !sessionData.logged_in
          ) {
            // Logged in but no broker, go to broker selection
            navigate('/broker', { replace: true })
            return
          }
        }
        // If session check fails (401, etc.), just stay on login page
      } catch (_err) {
      } finally {
        setIsCheckingSetup(false)
      }
    }
    checkSetup()
  }, [navigate])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsLoading(true)
    setError(null)

    try {
      // First, fetch CSRF token
      const csrfResponse = await fetch('/auth/csrf-token', {
        credentials: 'include',
      })

      if (!csrfResponse.ok) {
        setError('Failed to initialize login. Please refresh the page.')
        setIsLoading(false)
        return
      }

      const csrfData = await csrfResponse.json()

      // Create form data with CSRF token (matches original Flask template approach)
      const formData = new FormData()
      formData.append('username', username)
      formData.append('password', password)
      formData.append('csrf_token', csrfData.csrf_token)

      // Use native fetch like the original template
      const response = await fetch('/auth/login', {
        method: 'POST',
        body: formData,
        credentials: 'include',
      })

      // Check content type before parsing
      const contentType = response.headers.get('content-type')
      if (!contentType || !contentType.includes('application/json')) {
        // If redirected to setup page, inform user
        if (response.url.includes('/setup')) {
          setError('Please complete initial setup first.')
          navigate('/setup')
        } else {
          setError('Login failed. Please try again.')
        }
        setIsLoading(false)
        return
      }

      const data = await response.json()

      if (!response.ok || data.status === 'error') {
        setError(data.message || 'Login failed. Please try again.')
        if (data.redirect) {
          navigate(data.redirect)
        }
      } else if (data.status === 'totp_required') {
        // Server has accepted the password but won't issue a session
        // until TOTP verifies. Switch to the second-factor step.
        setStep('totp')
        setError(null)
      } else {
        // Set login state (broker from response if session was resumed, empty otherwise)
        setLogin(username, data.broker || '')
        showToast.success('Login successful', 'system')
        // Use redirect from response if provided, otherwise go to broker
        navigate(data.redirect || '/broker')
      }
    } catch (_err) {
      setError('Login failed. Please try again.')
    } finally {
      setIsLoading(false)
    }
  }

  const handleTotpSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsLoading(true)
    setError(null)

    try {
      const csrfResponse = await fetch('/auth/csrf-token', { credentials: 'include' })
      if (!csrfResponse.ok) {
        setError('Failed to verify TOTP. Please refresh the page.')
        setIsLoading(false)
        return
      }
      const { csrf_token } = await csrfResponse.json()

      const response = await fetch('/auth/login/totp', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf_token,
        },
        body: JSON.stringify({ totp_code: totpCode }),
      })

      const data = await response.json()

      if (response.status === 401 && data.message?.toLowerCase().includes('expired')) {
        // Pending login window timed out — bounce back to password step.
        setError(data.message)
        setStep('password')
        setTotpCode('')
        return
      }

      if (!response.ok || data.status === 'error') {
        setError(data.message || 'Invalid TOTP code.')
        setTotpCode('')
        return
      }

      setLogin(username, data.broker || '')
      showToast.success('Login successful', 'system')
      navigate(data.redirect || '/broker')
    } catch (_err) {
      setError('Failed to verify TOTP. Please try again.')
    } finally {
      setIsLoading(false)
    }
  }

  const handleBackToPassword = () => {
    setStep('password')
    setTotpCode('')
    setError(null)
  }

  // Show loading while checking setup
  if (isCheckingSetup) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background">
        <Loader2 aria-hidden="true" className="size-8 animate-spin text-[color:var(--brand)]" />
        <span className="sr-only">Loading</span>
      </div>
    )
  }

  return (
    <div className="relative flex min-h-dvh flex-col overflow-hidden bg-background">
      <Rings />

      <div className="relative mx-auto flex w-full max-w-md flex-1 flex-col px-6 pt-[clamp(2.5rem,12vh,6.25rem)] pb-7 md:justify-center md:gap-10 md:pt-12">
        {/* The name in both scripts is the page's heading (owner rule 2). */}
        <h1 className="m-0">
          <BrandLockup variant="stacked" size="lg" align="start" />
        </h1>

        <div className="mt-auto rounded-[1.25rem] border bg-card p-5 shadow-[var(--shadow-raised)] md:mt-0">
          {step === 'password' ? (
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="username" className={LABEL}>
                  Username
                </Label>
                <Input
                  id="username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  disabled={isLoading}
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  className={FIELD}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between gap-3">
                  <Label htmlFor="password" className={LABEL}>
                    Password
                  </Label>
                  <Link
                    to="/reset-password"
                    className="-my-3 inline-flex min-h-12 items-center rounded-lg px-1 text-[0.84375rem] font-semibold text-[color:var(--brand)] outline-none hover:text-[color:var(--brand-hover)] hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    Forgot password?
                  </Link>
                </div>
                <div className="relative flex">
                  <Input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    disabled={isLoading}
                    autoComplete="current-password"
                    className={cn(FIELD, 'pr-14')}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                    aria-pressed={showPassword}
                    className="absolute top-0 right-0 flex h-[3.25rem] w-12 items-center justify-center rounded-r-xl text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {showPassword ? (
                      <EyeOff aria-hidden="true" className="size-[1.375rem]" />
                    ) : (
                      <Eye aria-hidden="true" className="size-[1.375rem]" />
                    )}
                  </button>
                </div>
              </div>

              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}

              <button type="submit" disabled={isLoading} className={cn(PRIMARY, 'mt-0.5')}>
                {isLoading ? (
                  <>
                    <Loader2 aria-hidden="true" className="size-5 animate-spin" />
                    Logging in
                  </>
                ) : (
                  <>
                    Login
                    <ArrowRight
                      aria-hidden="true"
                      className="size-5 text-[color:var(--gold)]"
                      strokeWidth={2.4}
                    />
                  </>
                )}
              </button>

              <p className="flex items-center gap-2.5 px-0.5 pt-0.5 text-[0.84375rem] leading-snug font-medium text-muted-foreground">
                <Fingerprint
                  aria-hidden="true"
                  className="size-[1.375rem] shrink-0 text-[color:var(--brand)]"
                  strokeWidth={1.7}
                />
                Saved password? Chrome fills it after your fingerprint.
              </p>
            </form>
          ) : (
            <form onSubmit={handleTotpSubmit} className="flex flex-col gap-4">
              <div className="flex items-start gap-3">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-[color:var(--brand-tint)] text-[color:var(--brand)]">
                  <ShieldCheck aria-hidden="true" className="size-5" />
                </span>
                <div className="flex flex-col gap-0.5">
                  <h2 className="text-base font-bold">Two-factor authentication</h2>
                  <p className="text-sm leading-snug text-muted-foreground">
                    Enter the 6-digit code from your authenticator app to complete sign-in.
                  </p>
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="totp_code" className={LABEL}>
                  Authentication code
                </Label>
                <Input
                  id="totp_code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  disabled={isLoading}
                  autoFocus
                  required
                  className={cn(
                    FIELD,
                    'text-center text-[1.375rem] font-semibold tracking-[0.3em]'
                  )}
                />
              </div>

              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}

              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={handleBackToPassword}
                  disabled={isLoading}
                  className="inline-flex h-14 items-center justify-center gap-1.5 rounded-[0.875rem] border border-input bg-card px-4 text-base font-semibold text-foreground outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-60"
                >
                  <ArrowLeft aria-hidden="true" className="size-5" />
                  Back
                </button>
                <button
                  type="submit"
                  disabled={isLoading || totpCode.length !== 6}
                  className={cn(PRIMARY, 'flex-1')}
                >
                  {isLoading ? (
                    <>
                      <Loader2 aria-hidden="true" className="size-5 animate-spin" />
                      Verifying
                    </>
                  ) : (
                    'Verify code'
                  )}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
