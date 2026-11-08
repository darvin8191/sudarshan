import { ArrowDown, ArrowUp, Copy, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { ACTION, DANGER_FILL, OUTLINED } from '@/sudarshan/books/cards/parts'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { GROUP_TITLES } from './catalog'
import type { GroupKind } from './types'

export interface NodeMenu {
  what: 'condition' | 'group'
  /** The group's kind, for the choice of how many must match. */
  kind?: GroupKind
  canUp: boolean
  canDown: boolean
  /** Why Duplicate is off, or null. */
  noDuplicate: string | null
}

/**
 * A card's menu (design 5.2): Duplicate, Move up and down, Delete (which asks first), and for a
 * group how many of its conditions must match. "Is not" is never offered as a switch: it lives in
 * the operator list ("is at or below" rather than NOT).
 */
export function NodeMenuSheet({
  menu,
  onClose,
  onUp,
  onDown,
  onDuplicate,
  onDelete,
  onKind,
}: {
  menu: NodeMenu | null
  onClose: () => void
  onUp: () => void
  onDown: () => void
  onDuplicate: () => void
  onDelete: () => void
  onKind: (kind: GroupKind) => void
}) {
  const [asking, setAsking] = useState(false)
  const open = menu !== null
  useEffect(() => {
    if (open) setAsking(false)
  }, [open])
  const what = menu?.what ?? 'condition'
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={what === 'group' ? 'This group' : 'This condition'}
    >
      {menu && (
        <div className="flex flex-col gap-2">
          {menu.what === 'group' && menu.kind && (
            <fieldset
              data-group="match"
              aria-label="How many must match"
              className="m-0 min-w-0 flex-col gap-1.5 border-0 p-0 pb-1 flex"
            >
              {(['all', 'any', 'none'] as const).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  aria-pressed={menu.kind === kind}
                  onClick={() => onKind(kind)}
                  className={cn(
                    OUTLINED,
                    'justify-start',
                    menu.kind === kind &&
                      'border-primary bg-[color:var(--brand-tint)] font-bold text-primary'
                  )}
                >
                  {GROUP_TITLES[kind]}
                </button>
              ))}
            </fieldset>
          )}
          <button
            type="button"
            disabled={!menu.canUp}
            onClick={onUp}
            className={cn(OUTLINED, 'justify-start')}
          >
            <ArrowUp aria-hidden="true" className="size-5" />
            Move up
          </button>
          <button
            type="button"
            disabled={!menu.canDown}
            onClick={onDown}
            className={cn(OUTLINED, 'justify-start')}
          >
            <ArrowDown aria-hidden="true" className="size-5" />
            Move down
          </button>
          <button
            type="button"
            disabled={menu.noDuplicate !== null}
            onClick={onDuplicate}
            className={cn(OUTLINED, 'justify-start')}
          >
            <Copy aria-hidden="true" className="size-5" />
            Duplicate
          </button>
          {menu.noDuplicate && (
            <p className="px-1 text-[0.8125rem] font-medium">{menu.noDuplicate}</p>
          )}
          {asking ? (
            <div className="flex flex-col gap-2 rounded-xl border border-destructive p-3">
              <p className="text-sm font-semibold">
                {what === 'group'
                  ? 'Delete this group and its conditions?'
                  : 'Delete this condition?'}
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setAsking(false)}
                  className={cn(OUTLINED, 'flex-1')}
                >
                  Keep it
                </button>
                <button
                  type="button"
                  onClick={onDelete}
                  className={cn(ACTION, DANGER_FILL, 'flex-1')}
                >
                  <Trash2 aria-hidden="true" className="size-5" />
                  Delete
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setAsking(true)}
              className={cn(OUTLINED, 'justify-start text-destructive')}
            >
              <Trash2 aria-hidden="true" className="size-5" />
              Delete
            </button>
          )}
        </div>
      )}
    </BottomSheet>
  )
}
