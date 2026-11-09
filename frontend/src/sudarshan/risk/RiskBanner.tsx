import { Lock, OctagonX, TriangleAlert } from 'lucide-react'
import { useEffect, useReducer } from 'react'
import { Link } from 'react-router'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'
import {
  dayLocked,
  dayPaused,
  dayUncheckedUntil,
  killSwitchOn,
  killSwitchUnread,
  reachOf,
  useKillSwitch,
  useKillSwitchSync,
} from './killSwitch'

/** Where the switch reaches from the page the owner is on (Live, or the sandbox in Analyze). */
function useReach(): 'here' | 'sandbox' | 'none' {
  const state = useKillSwitch()
  const analyzer = useThemeStore((store) => store.appMode === 'analyzer')
  return reachOf(state?.stage, analyzer)
}

/** Which book's lock a page shows: Analyze mode's orders go to the sandbox. */
function useDayLocked(): boolean {
  const state = useKillSwitch()
  const analyzer = useThemeStore((store) => store.appMode === 'analyzer')
  return !killSwitchOn(state) && dayLocked(state, analyzer)
}

/**
 * Whether the daily loss limit is not being checked just now in the book the page shows (its
 * positions could not be read, so orders go unchecked). The line goes when the server's minute
 * is up: a timer for the words only, nothing is read.
 */
function useDayUnchecked(): boolean {
  const state = useKillSwitch()
  const analyzer = useThemeStore((store) => store.appMode === 'analyzer')
  const until = dayUncheckedUntil(state, analyzer)
  const [, render] = useReducer((count: number) => count + 1, 0)
  useEffect(() => {
    if (until === null) return
    const left = until - Date.now()
    if (left <= 0) return
    const timer = setTimeout(render, left + 50)
    return () => clearTimeout(timer)
  }, [until])
  if (killSwitchOn(state) || dayLocked(state, analyzer)) return false
  return until !== null && until > Date.now()
}

/** While the limit is not being checked: whether new orders are paused (today's figure could
 * not be read) rather than going unchecked (the book could not be read). */
function useDayPaused(): boolean {
  const state = useKillSwitch()
  const analyzer = useThemeStore((store) => store.appMode === 'analyzer')
  return dayPaused(state, analyzer)
}

/**
 * The kill switch's banner (M20.7), in both shells beside the Analyze banner: Layout above the
 * navbar, FullWidthLayout above the full-screen pages (/trading included). The words and an icon
 * say it, on the loss tint, so it is never colour alone; a tap opens Settings > Risk, where it
 * is resumed. Nothing while the switch is off or not known.
 *
 * When the daily loss limit has locked the day in the book the page trades (M5.14), the same
 * banner says that instead, with a lock on the attention tint; the kill switch, when both hold,
 * is the one said. While the limit cannot be checked it says that, with a warning sign, for as
 * long as the server does: orders go unchecked when the positions could not be read (any may be
 * an exit), new orders are paused when today's figure could not be read (exits still go).
 *
 * It also keeps the app-wide copy of the switch in step (useKillSwitchSync), so the chip in the
 * account controls and every page read the same state.
 */
export function RiskBanner() {
  const { socket } = useSocketContext()
  useKillSwitchSync(socket)
  const state = useKillSwitch()
  const locked = useDayLocked()
  const unchecked = useDayUnchecked()
  const paused = useDayPaused()
  const reach = useReach()
  if (killSwitchUnread(state)) {
    return (
      <output className="block" data-testid="risk-banner">
        <Link to="/risk" className="sd-risk-banner">
          <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
          <span>
            Kill switch state cannot be read <span aria-hidden="true">·</span> new orders are
            refused until it can
          </span>
        </Link>
      </output>
    )
  }
  if (killSwitchOn(state)) {
    return (
      // <output> is a status region, so the switch going on is announced.
      <output className="block" data-testid="risk-banner">
        <Link to="/risk" className="sd-risk-banner">
          <OctagonX aria-hidden="true" className="size-4 shrink-0" />
          {reach === 'here' ? (
            <span>
              Kill switch on <span aria-hidden="true">·</span> new orders refused{' '}
              <span aria-hidden="true">·</span> exits and cancels work
            </span>
          ) : reach === 'sandbox' ? (
            <span>
              Kill switch on for the sandbox only <span aria-hidden="true">·</span> Live orders are
              not checked
            </span>
          ) : (
            <span>
              Kill switch on, but order checks are off on this server{' '}
              <span aria-hidden="true">·</span> orders are not checked
            </span>
          )}
        </Link>
      </output>
    )
  }
  if (unchecked) {
    return (
      <output className="block" data-testid="risk-banner">
        <Link to="/risk" className="sd-risk-banner sd-risk-banner--day">
          <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
          {paused ? (
            <span>
              Daily loss limit not being checked <span aria-hidden="true">·</span> today's P&L
              cannot be read <span aria-hidden="true">·</span> new orders paused, exits go
            </span>
          ) : (
            <span>
              Daily loss limit not being checked <span aria-hidden="true">·</span> positions cannot
              be read <span aria-hidden="true">·</span> orders go meanwhile
            </span>
          )}
        </Link>
      </output>
    )
  }
  if (!locked) return null
  return (
    <output className="block" data-testid="risk-banner">
      <Link to="/risk" className="sd-risk-banner sd-risk-banner--day">
        <Lock aria-hidden="true" className="size-4 shrink-0" />
        <span>
          Daily loss limit reached <span aria-hidden="true">·</span> new orders refused until
          tomorrow <span aria-hidden="true">·</span> exits and cancels work
        </span>
      </Link>
    </output>
  )
}

/**
 * The same state as a chip in the account controls, which the navbar and the /trading toolbar
 * share. Not a control: the banner above both is the 48dp way to Settings > Risk, so this stays
 * small. The icon always; the words where there is room, and for a screen reader always.
 */
export function RiskChip({ variant = 'navbar' }: { variant?: 'navbar' | 'toolbar' }) {
  const state = useKillSwitch()
  const locked = useDayLocked()
  const unchecked = useDayUnchecked()
  const paused = useDayPaused()
  const reach = useReach()
  const unread = killSwitchUnread(state)
  const on = killSwitchOn(state)
  if (!on && !unread && !locked && !unchecked) return null
  const toolbar = variant === 'toolbar'
  const Icon = on ? OctagonX : unread ? TriangleAlert : locked ? Lock : TriangleAlert
  const title = unread
    ? 'Kill switch state cannot be read: new orders are refused until it can'
    : on
      ? reach === 'here'
        ? 'Kill switch on: new orders are refused'
        : reach === 'sandbox'
          ? 'Kill switch on for the sandbox only: Live orders are not checked'
          : 'Kill switch on, but order checks are off on this server'
      : locked
        ? 'Daily loss limit reached: new orders are refused until tomorrow'
        : paused
          ? "Daily loss limit not being checked: today's P&L cannot be read, so new orders are paused"
          : 'Daily loss limit not being checked: positions cannot be read'
  return (
    <span
      data-testid="risk-chip"
      title={title}
      className={cn(
        'sd-risk-chip inline-flex shrink-0 items-center gap-1.5 rounded-full border font-bold uppercase tracking-[0.06em]',
        !on && !unread && 'sd-risk-chip--day',
        toolbar ? 'h-6 px-2 text-[11px]' : 'h-7 px-2.5 text-xs'
      )}
    >
      <Icon aria-hidden="true" className={toolbar ? 'size-3.5' : 'size-4'} />
      <span className={toolbar ? 'sr-only min-[1800px]:not-sr-only' : 'sr-only sm:not-sr-only'}>
        {unread
          ? 'Kill switch unread'
          : on
            ? reach === 'sandbox'
              ? 'Sandbox kill switch on'
              : 'Kill switch on'
            : locked
              ? 'Day locked'
              : 'Limit not checked'}
      </span>
    </span>
  )
}

/**
 * The order ticket's line while the switch is on or the day is locked. Nothing on the ticket is
 * disabled, because a Buy can close a short; the line says what the server will do with the
 * order instead.
 */
export function KillSwitchNote() {
  const state = useKillSwitch()
  const locked = useDayLocked()
  const reach = useReach()
  if (killSwitchUnread(state)) {
    return (
      <p className="sd-risk-note" data-testid="kill-switch-note">
        <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
        Kill switch state cannot be read: a new order is refused until it can. An order that closes
        a position you hold still goes.
      </p>
    )
  }
  if (killSwitchOn(state)) {
    return (
      <p className="sd-risk-note" data-testid="kill-switch-note">
        <OctagonX aria-hidden="true" className="size-5 shrink-0" />
        {reach === 'here'
          ? 'Kill switch on: a new order is refused. An order that closes a position you hold still goes.'
          : reach === 'sandbox'
            ? 'Kill switch on for the sandbox only: Live orders are not checked.'
            : 'Kill switch on, but order checks are off on this server: orders are not checked.'}
      </p>
    )
  }
  if (!locked) return null
  return (
    <p className="sd-risk-note sd-risk-note--day" data-testid="kill-switch-note">
      <Lock aria-hidden="true" className="size-5 shrink-0" />
      Daily loss limit reached: a new order is refused until tomorrow. An order that closes a
      position you hold still goes.
    </p>
  )
}
