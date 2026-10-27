import { OctagonX } from 'lucide-react'
import { Link } from 'react-router'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { cn } from '@/lib/utils'
import { killSwitchOn, useKillSwitch, useKillSwitchSync } from './killSwitch'

/**
 * The kill switch's banner (M20.7), in both shells beside the Analyze banner: Layout above the
 * navbar, FullWidthLayout above the full-screen pages (/trading included). The words and an icon
 * say it, on the loss tint, so it is never colour alone; a tap opens Settings > Risk, where it
 * is resumed. Nothing while the switch is off or not known.
 *
 * It also keeps the app-wide copy of the switch in step (useKillSwitchSync), so the chip in the
 * account controls and every page read the same state.
 */
export function RiskBanner() {
  const { socket } = useSocketContext()
  useKillSwitchSync(socket)
  const state = useKillSwitch()
  if (!killSwitchOn(state)) return null

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

/**
 * The same state as a chip in the account controls, which the navbar and the /trading toolbar
 * share. Not a control: the banner above both is the 48dp way to Settings > Risk, so this stays
 * small. The icon always; the words where there is room, and for a screen reader always.
 */
export function RiskChip({ variant = 'navbar' }: { variant?: 'navbar' | 'toolbar' }) {
  const state = useKillSwitch()
  if (!killSwitchOn(state)) return null
  const toolbar = variant === 'toolbar'
  return (
    <span
      data-testid="risk-chip"
      title="Kill switch on: new orders are refused"
      className={cn(
        'sd-risk-chip inline-flex shrink-0 items-center gap-1.5 rounded-full border font-bold uppercase tracking-[0.06em]',
        toolbar ? 'h-6 px-2 text-[11px]' : 'h-7 px-2.5 text-xs'
      )}
    >
      <OctagonX aria-hidden="true" className={toolbar ? 'size-3.5' : 'size-4'} />
      <span className={toolbar ? 'sr-only min-[1800px]:not-sr-only' : 'sr-only sm:not-sr-only'}>
        Kill switch on
      </span>
    </span>
  )
}

/**
 * The order ticket's line while the switch is on. Nothing on the ticket is disabled, because a
 * Buy can close a short; the line says what the server will do with the order instead.
 */
export function KillSwitchNote() {
  const state = useKillSwitch()
  if (!killSwitchOn(state)) return null
  return (
    <p className="sd-risk-note" data-testid="kill-switch-note">
      <OctagonX aria-hidden="true" className="size-5 shrink-0" />
      Kill switch on: a new order is refused. An order that closes a position you hold still goes.
    </p>
  )
}
