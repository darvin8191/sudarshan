/**
 * SUDARSHAN: why a screen has no prices, in the same words on every screen
 * (the states are `usePriceAccess`'s, src/sudarshan/access.ts). Each says the
 * cause the server reported and where to act, with an icon beside the words,
 * never colour alone. Nothing is said once prices can be read.
 */
import { Hourglass, KeyRound, Unplug } from 'lucide-react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import type { PriceAccess } from './access'

export const ACCESS_WORDS = {
  checking: 'Checking your login',
  'upstox-logged-out':
    'Upstox is not logged in today, so there are no prices. Log in again with Test now on the Auto login page, or Reconnect on the Dashboard.',
  'no-api-key': 'Prices and search need an API key.',
} as const

/** A 48dp link in the brand's ink, as the notice's actions. */
const ACTION_LINK =
  'inline-flex min-h-12 items-center rounded-sm text-sm font-semibold text-[color:var(--brand)] underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring'

export function AccessNotice({ access, className }: { access: PriceAccess; className?: string }) {
  if (access.state === 'ready') return null

  // Still waiting for the server: a quiet line, and no claim about the cause.
  if (access.state === 'checking') {
    return (
      <output
        data-access="checking"
        className={cn(
          'flex min-h-5 items-center gap-1.5 px-1 text-xs font-medium text-muted-foreground',
          className
        )}
      >
        <Hourglass aria-hidden="true" className="size-3.5 shrink-0" />
        {ACCESS_WORDS.checking}
      </output>
    )
  }

  const loggedOut = access.state === 'upstox-logged-out'
  const Icon = loggedOut ? Unplug : KeyRound
  return (
    <div
      data-access={access.state}
      className={cn(
        'flex flex-col items-start rounded-[1.125rem] border border-[color:var(--gold)] bg-[color:var(--gold-tint)] px-4 pt-3 pb-0.5',
        className
      )}
    >
      <p className="flex items-start gap-2 text-sm font-semibold text-[color:var(--gold-ink)]">
        <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        {ACCESS_WORDS[access.state]}
      </p>
      {loggedOut ? (
        <div className="flex flex-wrap gap-x-6">
          <Link to="/autologin" className={ACTION_LINK}>
            Auto login
          </Link>
          <Link to="/dashboard" className={ACTION_LINK}>
            Dashboard
          </Link>
        </div>
      ) : (
        <Link to="/apikey" className={ACTION_LINK}>
          Create one under API Key
        </Link>
      )}
    </div>
  )
}
