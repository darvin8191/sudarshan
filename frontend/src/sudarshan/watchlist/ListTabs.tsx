import {
  ArrowUpDown,
  BellPlus,
  Check,
  FolderPlus,
  MoreVertical,
  Pencil,
  Plus,
  SlidersHorizontal,
  Trash2,
  Zap,
} from 'lucide-react'
import { type KeyboardEvent, type ReactNode, useEffect, useState } from 'react'
import type { Watchlist } from '@/api/watchlist'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { useOpenGuard } from './openGuard'
import { MAX_LISTS } from './useWatchlists'
import type { AlertOffer } from './v2/listAlert'

export const PANEL_ID = 'sd-watchlist-panel'
export const tabId = (listId: number) => `sd-watchlist-tab-${listId}`

const MENU_ITEM = 'min-h-12 gap-3 text-[0.9375rem] font-medium'

/** Why "New list" is off, in a trader's words. */
export const LISTS_FULL = `You have ${MAX_LISTS} lists, the most there can be. Delete one to make another.`

/**
 * The lists as tabs along the top, as a broker app shows them: the open one in
 * a tinted pill with a bold label (more than colour), its count beside each
 * name, the row scrolling sideways when the names do not fit. Arrow keys move
 * between tabs. The menu beside them makes, renames, edits and deletes lists;
 * at the server's cap of lists, "New list" is off and says why.
 */
export function ListTabs({
  lists,
  activeId,
  editing,
  onSelect,
  onCreate,
  onRename,
  onEdit,
  onDelete,
  fed,
  strip,
  onNewSection,
  newSectionOff,
  onView,
  alert,
  locked,
}: {
  lists: Watchlist[]
  activeId: number | null
  editing: boolean
  onSelect: (listId: number) => void
  onCreate: () => void
  onRename: () => void
  onEdit: () => void
  onDelete: () => void
  /** The lists a screener feeds (M9 UI7): each tab says Auto, an icon and a word. */
  fed?: ReadonlySet<number>
  /** v2 (M8.5): the favourite tabs and All lists, in place of the tab row; the menu stays. */
  strip?: ReactNode
  /** v2 (M8.4 F3): offers New section for an own list; absent for a built-in one. */
  onNewSection?: () => void
  /** Why New section is off (the list has 20), or null. */
  newSectionOff?: string | null
  /** F6 (v2): opens the View sheet; absent for a list the sheet does not apply to. */
  onView?: () => void
  /** F17: "Alert on this list", for the list on show; off with its reason, never hidden. */
  alert?: AlertOffer
  /** F10: a built-in list is on show. Rename, Reorder or remove and Delete are absent, not dead. */
  locked?: boolean
}) {
  // F3: with v2 on the menu ignores taps for its first half second (openGuard.ts).
  const [menuOpen, setMenuOpen] = useState(false)
  const menuGuard = useOpenGuard(menuOpen)
  // Keep the open tab in view when it changes, from a menu or a restored session.
  useEffect(() => {
    if (activeId === null) return
    const tab = document.getElementById(tabId(activeId))
    if (tab && typeof tab.scrollIntoView === 'function') {
      tab.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    }
  }, [activeId])

  const full = lists.length >= MAX_LISTS

  const step = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const count = lists.length
    const to: Record<string, number> = {
      ArrowRight: (index + 1) % count,
      ArrowLeft: (index - 1 + count) % count,
      Home: 0,
      End: count - 1,
    }
    const target = lists[to[event.key] ?? -1]
    if (!target) return
    event.preventDefault()
    onSelect(target.id)
    document.getElementById(tabId(target.id))?.focus()
  }

  return (
    <div className="flex items-center gap-1">
      {strip ?? (
        <div
          role="tablist"
          aria-label="Watchlists"
          className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {lists.map((list, index) => {
            const active = list.id === activeId
            return (
              <button
                key={list.id}
                id={tabId(list.id)}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls={PANEL_ID}
                tabIndex={active ? 0 : -1}
                onClick={() => onSelect(list.id)}
                onKeyDown={(event) => step(event, index)}
                className="group flex h-12 shrink-0 items-center outline-none"
              >
                <span
                  className={cn(
                    'flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm group-focus-visible:ring-2 group-focus-visible:ring-ring',
                    active
                      ? 'border-primary bg-[color:var(--primary-tint)] font-bold text-primary'
                      : 'border-border bg-card font-semibold text-[color:var(--text-secondary)]'
                  )}
                >
                  <span className="max-w-[11rem] truncate">{list.name}</span>
                  {fed?.has(list.id) && (
                    <span
                      data-auto="true"
                      className="inline-flex items-center gap-0.5 text-[0.6875rem] font-bold"
                    >
                      <Zap aria-hidden="true" className="size-3" />
                      Auto
                    </span>
                  )}
                  <span className="text-xs font-semibold opacity-80">{list.items.length}</span>
                </span>
              </button>
            )
          })}
        </div>
      )}

      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger
          aria-label="List options"
          className="flex size-12 shrink-0 items-center justify-center rounded-xl text-[color:var(--text-secondary)] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
        >
          <MoreVertical aria-hidden="true" className="size-5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="w-56"
          {...(strip ? menuGuard.handlers : {})}
          data-settling={(strip && menuGuard.settling) || undefined}
        >
          <DropdownMenuItem
            className={MENU_ITEM}
            disabled={full}
            aria-describedby={full ? 'sd-lists-full' : undefined}
            onSelect={onCreate}
          >
            <Plus aria-hidden="true" className="size-5" />
            New list
          </DropdownMenuItem>
          {full && (
            <p
              id="sd-lists-full"
              className="px-2 pb-2 text-xs leading-snug font-medium text-muted-foreground"
            >
              {LISTS_FULL}
            </p>
          )}
          {!locked && (
            <DropdownMenuItem
              className={MENU_ITEM}
              disabled={activeId === null}
              onSelect={onRename}
            >
              <Pencil aria-hidden="true" className="size-5" />
              Rename list
            </DropdownMenuItem>
          )}
          {onNewSection && (
            <>
              <DropdownMenuItem
                className={MENU_ITEM}
                disabled={activeId === null || !!newSectionOff}
                aria-describedby={newSectionOff ? 'sd-sections-full' : undefined}
                onSelect={onNewSection}
              >
                <FolderPlus aria-hidden="true" className="size-5" />
                New section
              </DropdownMenuItem>
              {newSectionOff && (
                <p
                  id="sd-sections-full"
                  className="px-2 pb-2 text-xs leading-snug font-medium text-muted-foreground"
                >
                  {newSectionOff}
                </p>
              )}
            </>
          )}
          {onView && (
            <DropdownMenuItem className={MENU_ITEM} disabled={activeId === null} onSelect={onView}>
              <SlidersHorizontal aria-hidden="true" className="size-5" />
              View
            </DropdownMenuItem>
          )}
          {alert && (
            <>
              <DropdownMenuItem
                className={MENU_ITEM}
                disabled={activeId === null || alert.href === null}
                aria-describedby={alert.off ? 'sd-list-alert-off' : undefined}
                onSelect={() => alert.href && alert.go(alert.href)}
              >
                <BellPlus aria-hidden="true" className="size-5" />
                Alert on this list
              </DropdownMenuItem>
              {alert.off && (
                <p
                  id="sd-list-alert-off"
                  className="px-2 pb-2 text-xs leading-snug font-medium text-muted-foreground"
                >
                  {alert.off}
                </p>
              )}
            </>
          )}
          {!locked && (
            <>
              <DropdownMenuItem
                className={MENU_ITEM}
                disabled={activeId === null}
                onSelect={onEdit}
              >
                {editing ? (
                  <Check aria-hidden="true" className="size-5" />
                ) : (
                  <ArrowUpDown aria-hidden="true" className="size-5" />
                )}
                {editing ? 'Done editing' : 'Reorder or remove'}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className={cn(MENU_ITEM, 'text-[color:var(--down)] focus:text-[color:var(--down)]')}
                disabled={activeId === null}
                onSelect={onDelete}
              >
                <Trash2 aria-hidden="true" className="size-5 text-current" />
                Delete list
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
