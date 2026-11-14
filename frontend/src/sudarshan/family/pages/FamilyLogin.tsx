// SUDARSHAN P6 D3 (M2.10): signing in to the family view: sign-in name, password and the 6-digit
// code. A public page, outside the shell.
//
// One sentence answers a wrong name, a wrong password and a wrong code alike, on purpose (the
// service words it and this page shows it as it came): a different answer for each would tell
// someone which part they already have right. The lock-out sentence names the time to try again.
import { useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useEffect, useState } from 'react'
import { Navigate } from 'react-router'
import { BrandLockup } from '@/sudarshan/BrandLockup'
import { FamilyError, familyApi } from '../api'
import { FamilyBadge } from '../FamilyBadge'
import { Field, PRIMARY, Problem } from '../forms'
import { forgetMember, ME_KEY } from '../session'

export const FORGOT =
  'Forgot? Ask the account owner to send a new invite; it replaces your password and code app.'

export default function FamilyLogin() {
  const client = useQueryClient()
  const [handle, setHandle] = useState('')
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [signedIn, setSignedIn] = useState(false)

  // The service hands out the page's CSRF cookie with any answer it gives, so a first look at
  // `me` is what lets the sign-in below be sent at all. A phone that is already signed in
  // goes straight on. Whatever the page held of the member before is forgotten first: every way a
  // session ends leads here (review M5).
  useEffect(() => {
    let live = true
    forgetMember(client)
    familyApi
      .me()
      .then((me) => {
        if (!live) return
        client.setQueryData(ME_KEY, me)
        setSignedIn(true)
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [client])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    try {
      const me = await familyApi.login({
        handle: handle.trim(),
        password,
        totp: totp.replace(/\s+/g, ''),
      })
      forgetMember(client) // nothing read for anyone before may show for this member
      client.setQueryData(ME_KEY, me)
      setPassword('')
      setTotp('')
      setSignedIn(true)
    } catch (error) {
      setProblem(
        error instanceof FamilyError ? error.message : 'That could not be sent. Try again.'
      )
      setTotp('')
    } finally {
      setBusy(false)
    }
  }

  if (signedIn) return <Navigate to="/family" replace />

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-6">
      <form
        onSubmit={submit}
        noValidate
        className="flex w-full max-w-sm flex-col items-start gap-4 rounded-[1.125rem] border bg-card px-5 py-6 shadow-[var(--shadow-card)]"
      >
        <BrandLockup variant="stacked" align="start" />
        <FamilyBadge />
        <h1 className="sd-display text-[1.5rem] font-bold">Sign in</h1>
        <div className="flex w-full flex-col gap-4">
          <Field
            label="Sign-in name"
            value={handle}
            onChange={(event) => setHandle(event.target.value.toLowerCase())}
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
          />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
          />
          <Field
            label="6-digit code"
            value={totp}
            onChange={(event) => setTotp(event.target.value.replace(/[^\d\s]/g, ''))}
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={7}
          />
        </div>
        {problem && <Problem text={problem} />}
        <button
          type="submit"
          disabled={busy || handle === '' || password === '' || totp === ''}
          className={`${PRIMARY} w-full`}
        >
          Sign in
        </button>
        <p className="text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">{FORGOT}</p>
      </form>
    </div>
  )
}
