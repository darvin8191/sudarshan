import { ChevronDown, ChevronRight, MoreVertical } from 'lucide-react'
import { cn } from '@/lib/utils'
import { MarkGlyph, markOf } from './marks'
import type { Section } from './v2/types'

/**
 * A section's header inside the list's card (M8.4 F3): a 48 dp row with a 4 px stripe and the glyph
 * of its mark (none for mark 0), the name and the count ("Banks · 8"), and a chevron that folds or
 * unfolds the rows under it. A menu button of 48 dp ends it. Nothing is shown by colour alone: the
 * stripe has its glyph, the fold has its chevron and its word.
 */
export function SectionHeader({
  section,
  count,
  collapsed,
  onToggle,
  onMenu,
}: {
  section: Section
  count: number
  collapsed: boolean
  onToggle: (id: number) => void
  onMenu: (id: number) => void
}) {
  const look = markOf(section.mark)
  const Chevron = collapsed ? ChevronRight : ChevronDown
  return (
    <li
      data-section={section.id}
      className="flex min-h-12 items-stretch bg-[color:var(--surface-2)]"
    >
      <span
        aria-hidden="true"
        data-stripe={look ? section.mark : undefined}
        className="w-1 shrink-0"
        style={{ backgroundColor: look?.color }}
      />
      <button
        type="button"
        aria-expanded={!collapsed}
        onClick={() => onToggle(section.id)}
        className="flex min-h-12 min-w-0 flex-1 items-center gap-2 pr-1 pl-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <MarkGlyph mark={section.mark} />
        <span className="min-w-0 truncate text-[0.9375rem] font-bold">
          {section.name} · {count}
        </span>
        <Chevron aria-hidden="true" className={cn('ml-auto size-5 shrink-0')} />
        <span className="sr-only">{collapsed ? 'Expand' : 'Collapse'}</span>
      </button>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-label={`Options for ${section.name}`}
        onClick={() => onMenu(section.id)}
        className="flex size-12 shrink-0 items-center justify-center text-[color:var(--text-secondary)] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <MoreVertical aria-hidden="true" className="size-5" />
      </button>
    </li>
  )
}
