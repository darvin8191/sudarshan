import { EllipsisVertical, Plus } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { OUTLINED, UNAVAILABLE } from '@/sudarshan/books/cards/parts'
import { GROUP_TITLES } from './catalog'
import type { GroupKind } from './types'

/**
 * A group as a card that holds cards (design 5.2): "Match all of these", "Match any of these",
 * "Match none of these". The top group has no menu (it cannot be moved or deleted); the add
 * buttons say why they are off at the third level or the twentieth condition.
 */
export function GroupCard({
  kind,
  level,
  onMenu,
  onAddClause,
  onAddGroup,
  whyNoClause,
  whyNoGroup,
  empty,
  children,
}: {
  kind: GroupKind
  level: number
  onMenu?: () => void
  onAddClause: () => void
  onAddGroup: () => void
  whyNoClause: string | null
  whyNoGroup: string | null
  empty: boolean
  children: ReactNode
}) {
  return (
    <section
      data-group={kind}
      data-level={level}
      aria-label={GROUP_TITLES[kind]}
      className={cn(
        'flex flex-col gap-2 rounded-[1.125rem] border p-2',
        level === 1
          ? 'bg-[color:var(--surface-2)]'
          : 'border-[color:var(--border-strong)] bg-background'
      )}
    >
      <div className="flex items-center justify-between gap-2 pl-1">
        <h3 className="text-[0.9375rem] font-bold">{GROUP_TITLES[kind]}</h3>
        {onMenu && (
          <button
            type="button"
            aria-label={`More for the group: ${GROUP_TITLES[kind]}`}
            onClick={onMenu}
            className="flex size-12 items-center justify-center rounded-xl text-[color:var(--text-secondary)] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
          >
            <EllipsisVertical aria-hidden="true" className="size-5" />
          </button>
        )}
      </div>
      {empty && (
        <p className="px-1 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]">
          This group has no conditions. Add one, or delete the group.
        </p>
      )}
      {children}
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={whyNoClause !== null}
            onClick={onAddClause}
            className={whyNoClause ? UNAVAILABLE : OUTLINED}
          >
            <Plus aria-hidden="true" className="size-4" />
            Add a condition
          </button>
          <button
            type="button"
            disabled={whyNoGroup !== null}
            onClick={onAddGroup}
            className={whyNoGroup ? UNAVAILABLE : OUTLINED}
          >
            <Plus aria-hidden="true" className="size-4" />
            Add a group
          </button>
        </div>
        {(whyNoClause ?? whyNoGroup) && (
          <p className="px-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
            {whyNoClause ?? whyNoGroup}
          </p>
        )}
      </div>
    </section>
  )
}
