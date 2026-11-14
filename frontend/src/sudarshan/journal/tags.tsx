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

/**
 * A card's mistakes as one chip with their count (review L8): a row of identical warning glyphs
 * said nothing on a phone, where nothing hovers. The names are read out and shown on a long press.
 */
export function MistakeCount({ tags }: { tags: readonly Tag[] }) {
  if (tags.length === 0) return null
  const names = tags.map((tag) => tag.name).join(', ')
  return (
    <span
      data-testid="trip-tags"
      title={names}
      className={cn(
        'inline-flex h-6 items-center gap-1 rounded-full border px-2 text-xs font-semibold',
        TAG_TONE.neutral
      )}
    >
      <TagGlyph kind="mistake" />
      {tags.length} {tags.length === 1 ? 'mistake' : 'mistakes'}
      {tags.map((tag) => (
        <span key={tag.id} className="sr-only">
          {`, ${tag.name}`}
        </span>
      ))}
    </span>
  )
}
