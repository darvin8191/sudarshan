import {
  Ban,
  CircleHelp,
  CirclePlus,
  Clock,
  Hourglass,
  Info,
  type LucideIcon,
  Pin,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Chip } from './types'

/**
 * The tag on a row a screener feeds (design 4.3): always an icon and a word, colours from the D
 * tokens. The words, the icon's name and the tone come from the service (words.py `chip`), so the
 * phone and Telegram say the same; this only draws them. A screen reader gets the longer name.
 */
const ICONS: Record<string, LucideIcon> = {
  'circle-plus': CirclePlus,
  clock: Clock,
  hourglass: Hourglass,
  pin: Pin,
  ban: Ban,
  info: Info,
}

const TONES: Record<Chip['tone'], string> = {
  brand: 'bg-[color:var(--brand-tint)] text-[color:var(--brand)]',
  quiet: 'bg-[color:var(--divider)] text-[color:var(--text-secondary)]',
  amber: 'bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]',
  outline: 'border border-[color:var(--brand)] bg-card text-[color:var(--brand)]',
}

export function ChipMark({ chip, className }: { chip: Chip; className?: string }) {
  const Icon = ICONS[chip.icon] ?? CircleHelp
  return (
    <span
      data-chip={chip.kind}
      role="img"
      aria-label={chip.name}
      className={cn(
        'inline-flex h-6 shrink-0 items-center gap-1 rounded-md px-2 text-[0.71875rem] leading-none font-bold',
        TONES[chip.tone] ?? TONES.quiet,
        className
      )}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      <span aria-hidden="true">{chip.text}</span>
    </span>
  )
}
