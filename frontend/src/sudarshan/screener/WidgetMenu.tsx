import { ArrowDown, ArrowUp, ExternalLink, MoreVertical, Settings, Trash2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import type { WidgetCard } from './types'

/** The three-dot button at the right of a card's head: 48 by 48 dp, named for its screen. */
export function OptionsButton({ name, onOpen }: { name: string; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`Widget options for ${name}`}
      className="flex size-12 shrink-0 items-center justify-center rounded-xl text-[color:var(--text-secondary)] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
    >
      <MoreVertical aria-hidden="true" className="size-5" />
    </button>
  )
}

function Item({
  icon,
  label,
  onClick,
  disabled,
  danger,
}: {
  icon: ReactNode
  label: string
  onClick: () => void
  disabled?: boolean
  danger?: boolean
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'flex min-h-12 w-full items-center gap-3 rounded-xl px-3 text-left text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
        danger && 'text-destructive'
      )}
    >
      {icon}
      {label}
    </button>
  )
}

/**
 * A widget's options (design 3.9): Settings, Move up, Move down, Open screen and Remove from
 * dashboard. The card is named by its screen and kind in the address, so a suggested widget at
 * version 0 has the same menu. The sheet itself ignores taps for half a second after it opens.
 */
export function WidgetMenu({
  card,
  name,
  first,
  last,
  busy,
  onSettings,
  onMove,
  onOpenScreen,
  onRemove,
  onClose,
}: {
  card: WidgetCard | null
  name: string
  first: boolean
  last: boolean
  busy: boolean
  onSettings: (card: WidgetCard) => void
  onMove: (card: WidgetCard, by: -1 | 1) => void
  onOpenScreen: (id: number) => void
  onRemove: (card: WidgetCard) => void
  onClose: () => void
}) {
  const icon = 'size-5 shrink-0'
  return (
    <BottomSheet
      open={card !== null}
      onOpenChange={(next) => !next && onClose()}
      title="Widget options"
      description={name}
    >
      {card && (
        <div className="flex flex-col gap-1">
          <Item
            icon={<Settings aria-hidden="true" className={icon} />}
            label="Settings"
            onClick={() => onSettings(card)}
          />
          <Item
            icon={<ArrowUp aria-hidden="true" className={icon} />}
            label="Move up"
            disabled={first || busy}
            onClick={() => onMove(card, -1)}
          />
          <Item
            icon={<ArrowDown aria-hidden="true" className={icon} />}
            label="Move down"
            disabled={last || busy}
            onClick={() => onMove(card, 1)}
          />
          <Item
            icon={<ExternalLink aria-hidden="true" className={icon} />}
            label="Open screen"
            onClick={() => onOpenScreen(card.screen_id)}
          />
          <Item
            icon={<Trash2 aria-hidden="true" className={icon} />}
            label="Remove from dashboard"
            danger
            disabled={busy}
            onClick={() => onRemove(card)}
          />
        </div>
      )}
    </BottomSheet>
  )
}
