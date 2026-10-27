import { Lock, OctagonX } from 'lucide-react'
import { Link } from 'react-router'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'
import { dayLocked, killSwitchOn, useKillSwitch, useKillSwitchSync } from './killSwitch'

/** Which book's lock a page shows: Analyze mode's orders go to the sandbox. */
function useDayLocked(): boolean {
  const state = useKillSwitch()
  const analyzer = useThemeStore((store) => store.appMode === 'analyzer')
  return !killSwitchOn(state) && dayLocked(state, analyzer)
}

/**
 * The kill switch's banner (M20.7), in both shells beside the Analyze banner: Layout above the
 * navbar, FullWidthLayout above the full-screen pages (/trading included). The words and an icon
 * say it, on the loss tint, so it is never colour alone; a tap opens Settings > Risk, where it
 * is resumed. Nothing while the switch is off or not known.
 *
 * When the daily loss limit has locked the day in the book the page trades (M5.14), the same
 * banner says that instead, with a lock on the attention tint; the kill switch, when both hold,
 * is the one said.
 *
 * It also keeps the app-wide copy of the switch in step (useKillSwitchSync), so the chip in the
 * account controls and every page read the same state.
 */
export function RiskBanner() {
  const { socket } = useSocketContext()
  useKillSwitchSync(socket)
  const state = useKillSwitch()
  const locked = useDayLocked()
  if (killSwitchOn(state)) {
    return (
      // <output> is a status region, so the switch going on is announced.
      <output className="block" data-testid="risk-banner">
        <Link to="/risk" className="sd-risk-banner">
          <OctagonX aria-hidden="true" className="size-4 shrink-0" />
          <span>
            Kill switch on <span aria-hidden="true">·</span> new orders refused{' '}
            <span aria-hidden="true">·</span> exits and cancels work
          </span>
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
  const on = killSwitchOn(state)
  if (!on && !locked) return null
  const toolbar = variant === 'toolbar'
  const Icon = on ? OctagonX : Lock
  return (
    <span
      data-testid="risk-chip"
      title={
        on
          ? 'Kill switch on: new orders are refused'
          : 'Daily loss limit reached: new orders are refused until tomorrow'
      }
      className={cn(
        'sd-risk-chip inline-flex shrink-0 items-center gap-1.5 rounded-full border font-bold uppercase tracking-[0.06em]',
        !on && 'sd-risk-chip--day',
        toolbar ? 'h-6 px-2 text-[11px]' : 'h-7 px-2.5 text-xs'
      )}
    >
      <Icon aria-hidden="true" className={toolbar ? 'size-3.5' : 'size-4'} />
      <span className={toolbar ? 'sr-only min-[1800px]:not-sr-only' : 'sr-only sm:not-sr-only'}>
        {on ? 'Kill switch on' : 'Day locked'}
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
  if (killSwitchOn(state)) {
    return (
      <p className="sd-risk-note" data-testid="kill-switch-note">
        <OctagonX aria-hidden="true" className="size-5 shrink-0" />
        Kill switch on: a new order is refused. An order that closes a position you hold still goes.
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
