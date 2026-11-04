import { Archive, ArchiveRestore, ChevronLeft, ChevronRight, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { portfolioCall } from '../api'
import type { TabColour, TabIcon, TabInfo } from '../types'
import {
  ColourChoices,
  DANGER,
  FIELD,
  IconChoices,
  LABEL,
  NameFields,
  PinnedField,
  PRIMARY,
  ProblemLine,
  SECONDARY,
} from './TabFields'
import { useSend } from './useSend'

/**
 * "Tab settings": rename, second name, icon, colour, pinned, position, archive and delete. A
 * change is sent with the tab's version and only what changed; a tab changed on another screen
 * answers with the server's sentence and nothing is saved. Delete is offered for an archived tab
 * only and needs its name typed. Every request is sent once.
 */
export function TabSettingsSheet({
  open,
  onClose,
  tab,
  siblings,
  onChanged,
  onDeleted,
}: {
  open: boolean
  onClose: () => void
  tab: TabInfo | null
  /** The custom tabs that are not archived, in order: what "earlier" and "later" move among. */
  siblings: readonly TabInfo[]
  onChanged: () => void
  onDeleted: (id: number) => void
}) {
  const [name, setName] = useState('')
  const [nameAlt, setNameAlt] = useState('')
  const [icon, setIcon] = useState<TabIcon>('briefcase')
  const [colour, setColour] = useState<TabColour>('brand')
  const [pinned, setPinned] = useState(false)
  const [typed, setTyped] = useState('')
  const send = useSend()

  // biome-ignore lint/correctness/useExhaustiveDependencies: a fresh form on each opening, or when the tab changes
  useEffect(() => {
    if (!open || !tab) return
    setName(tab.name)
    setNameAlt(tab.name_alt ?? '')
    setIcon(tab.icon)
    setColour(tab.colour)
    setPinned(tab.pinned)
    setTyped('')
    send.clear()
  }, [open, tab?.id, tab?.version])

  if (!tab) return null

  const changes: Record<string, unknown> = {}
  if (name.trim() !== tab.name) changes.name = name.trim()
  if ((nameAlt.trim() || null) !== tab.name_alt) changes.name_alt = nameAlt.trim() || null
  if (icon !== tab.icon) changes.icon = icon
  if (colour !== tab.colour) changes.colour = colour
  if (pinned !== tab.pinned) changes.pinned = pinned
  const dirty = Object.keys(changes).length > 0 && name.trim().length > 0

  const update = async (fields: Record<string, unknown>, then: () => void = onChanged) => {
    const ok = await send.run(async () => {
      const result = await portfolioCall('tab_update', {
        id: tab.id,
        version: tab.version,
        ...fields,
      })
      if (!result.enabled) throw new Error(result.message)
    })
    if (ok) then()
  }

  const at = siblings.findIndex((s) => s.id === tab.id)
  const earlier = at > 0 ? siblings[at - 1] : null
  const later = at >= 0 && at < siblings.length - 1 ? siblings[at + 1] : null

  const remove = async () => {
    const ok = await send.run(async () => {
      const result = await portfolioCall('tab_delete', {
        id: tab.id,
        version: tab.version,
        confirm_name: typed,
      })
      if (!result.enabled) throw new Error(result.message)
    })
    if (ok) onDeleted(tab.id)
  }

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Tab settings"
      description={tab.archived ? `${tab.name} · archived` : tab.name}
    >
      <div className="flex flex-col gap-4 overflow-y-auto px-4 pb-4">
        <NameFields
          name={name}
          nameAlt={nameAlt}
          onName={setName}
          onNameAlt={setNameAlt}
          disabled={send.pending}
        />
        <IconChoices value={icon} onChange={setIcon} disabled={send.pending} />
        <ColourChoices value={colour} onChange={setColour} disabled={send.pending} />
        <PinnedField value={pinned} onChange={setPinned} disabled={send.pending} />

        {!tab.archived && (
          <div className="flex flex-col gap-1.5">
            <span className={LABEL}>Position</span>
            <div className="flex gap-2">
              <button
                type="button"
                className={`${SECONDARY} flex-1`}
                disabled={!earlier || send.pending || dirty}
                onClick={() => earlier && void update({ position: earlier.position })}
              >
                <ChevronLeft aria-hidden="true" className="size-4" />
                Move earlier
              </button>
              <button
                type="button"
                className={`${SECONDARY} flex-1`}
                disabled={!later || send.pending || dirty}
                onClick={() => later && void update({ position: later.position })}
              >
                Move later
                <ChevronRight aria-hidden="true" className="size-4" />
              </button>
            </div>
          </div>
        )}

        <ProblemLine problem={send.problem} />

        <button
          type="button"
          className={PRIMARY}
          disabled={!dirty || send.pending}
          onClick={() => void update(changes)}
        >
          {send.pending ? 'Saving' : 'Save changes'}
        </button>

        <div className="flex flex-col gap-2 border-t border-[color:var(--divider)] pt-4">
          <p className="text-sm text-[color:var(--text-secondary)]">
            {tab.archived
              ? 'This tab is hidden. Everything in it is kept.'
              : 'Archiving hides the tab and keeps everything in it.'}
          </p>
          <button
            type="button"
            className={SECONDARY}
            disabled={send.pending}
            onClick={() => void update({ archived: !tab.archived })}
          >
            {tab.archived ? (
              <ArchiveRestore aria-hidden="true" className="size-4" />
            ) : (
              <Archive aria-hidden="true" className="size-4" />
            )}
            {tab.archived ? 'Restore this tab' : 'Archive this tab'}
          </button>
        </div>

        {tab.archived && (
          <div className="flex flex-col gap-2 border-t border-[color:var(--divider)] pt-4">
            <p className="text-sm font-semibold">Delete this tab for good</p>
            <p className="text-sm text-[color:var(--text-secondary)]">
              Its entries go with it and cannot be brought back. Type the tab’s name to confirm.
            </p>
            <input
              className={FIELD}
              aria-label="Type the tab’s name to confirm"
              value={typed}
              disabled={send.pending}
              onChange={(event) => setTyped(event.target.value)}
              autoComplete="off"
            />
            <button
              type="button"
              className={DANGER}
              disabled={typed !== tab.name || send.pending}
              onClick={() => void remove()}
            >
              <Trash2 aria-hidden="true" className="size-4" />
              Delete this tab
            </button>
          </div>
        )}
      </div>
    </BottomSheet>
  )
}
