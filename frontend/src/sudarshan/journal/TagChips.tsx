import { Check, Plus } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { Problem } from './parts'
import { TAG_TONE, TagGlyph } from './tags'
import { LIMITS, type Tag } from './types'

/**
 * Mistake tags as chips to switch on and off: each its icon and its word, a tick while on (never
 * colour alone), a 48 dp target. Archived tags stay on a trade that has them and are not offered
 * to new ones.
 */
export function TagChips({
  tags,
  selected,
  onToggle,
  disabled = false,
}: {
  tags: readonly Tag[]
  selected: readonly number[]
  onToggle: (id: number) => void
  disabled?: boolean
}) {
  const shown = tags.filter((tag) => !tag.archived || selected.includes(tag.id))
  if (shown.length === 0) {
    return (
      <p className="text-sm text-[color:var(--text-secondary)]">
        No mistake tags yet. Add one below.
      </p>
    )
  }
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Mistake tags">
      {shown.map((tag) => {
        const on = selected.includes(tag.id)
        return (
          <li key={tag.id}>
            <button
              type="button"
              aria-pressed={on}
              disabled={disabled}
              onClick={() => onToggle(tag.id)}
              className={cn(
                'inline-flex min-h-12 items-center gap-1.5 rounded-full border px-3.5 text-[0.875rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60',
                TAG_TONE[tag.colour],
                on && 'ring-2 ring-[color:var(--text-primary)]'
              )}
            >
              <TagGlyph kind={tag.kind} className="size-4" />
              {tag.name}
              {on && <Check aria-hidden="true" className="size-4" strokeWidth={3} />}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

/** A new mistake tag by name: up to 40 characters, one tap. */
export function NewTag({
  onAdd,
  busy,
  problem,
}: {
  onAdd: (name: string) => Promise<boolean>
  busy: boolean
  problem: string | null
}) {
  const [name, setName] = useState('')
  const trimmed = name.trim()
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <input
          type="text"
          aria-label="New mistake tag"
          placeholder="New mistake tag"
          maxLength={LIMITS.tag_name}
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="min-h-12 min-w-0 flex-1 rounded-[0.875rem] border border-input bg-card px-3 text-[0.9375rem]"
        />
        <button
          type="button"
          disabled={busy || trimmed === ''}
          onClick={async () => {
            if (await onAdd(trimmed)) setName('')
          }}
          className="inline-flex min-h-12 items-center gap-1.5 rounded-[0.875rem] border border-input bg-card px-3.5 text-[0.9375rem] font-semibold hover:bg-accent disabled:opacity-60"
        >
          <Plus aria-hidden="true" className="size-4" />
          Add tag
        </button>
      </div>
      {problem && <Problem>{problem}</Problem>}
    </div>
  )
}
