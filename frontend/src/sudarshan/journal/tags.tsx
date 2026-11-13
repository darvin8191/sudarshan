import { Tag as TagIcon, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Tag, TagColour } from './types'

/**
 * A tag's colours (design 6.2, cross-design Y40). Colour is never the only sign: every tag is its
 * icon and its word. `gold` is decoration only, so a gold tag is the gold tint with ink #8A5A00,
 * never gold text. A mistake tag takes the warning icon, a label the tag icon.
 */
export const TAG_TONE: Record<TagColour, string> = {
  brand: 'border-[color:var(--brand)]/30 bg-[color:var(--brand-tint)] text-[color:var(--brand)]',
  gold: 'border-[color:var(--gold)]/50 bg-[color:var(--gold-tint)] text-[color:var(--gold-ink)]',
  info: 'border-[color:var(--info)]/30 bg-[color:var(--brand-tint)] text-[color:var(--info)]',
  neutral: 'border-[color:var(--border-strong)] bg-card text-[color:var(--text-secondary)]',
  loss: 'border-[color:var(--down)]/30 bg-[color:var(--down-tint)] text-[color:var(--down)]',
}

export function TagGlyph({ kind, className }: { kind: Tag['kind']; className?: string }) {
  const Icon = kind === 'mistake' ? TriangleAlert : TagIcon
  return <Icon aria-hidden="true" className={cn('size-3.5 shrink-0', className)} />
}

/** Icons only, for a crowded row: the words are read out and shown on hover. */
export function TagIcons({ tags }: { tags: readonly Tag[] }) {
  if (tags.length === 0) return null
  return (
    <span className="inline-flex items-center gap-1" data-testid="trip-tags">
      {tags.map((tag) => (
        <span
          key={tag.id}
          title={tag.name}
          className={cn(
            'inline-flex size-6 items-center justify-center rounded-full border',
            TAG_TONE[tag.colour]
          )}
        >
          <TagGlyph kind={tag.kind} />
          <span className="sr-only">{tag.name}</span>
        </span>
      ))}
    </span>
  )
}
