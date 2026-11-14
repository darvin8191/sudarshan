// SUDARSHAN P6 D7b (M8.15, M7.37): what the owner shares with the family. Watchlists (pick lists,
// for one member or every member), Books (no switch: one line saying what decides which books a
// member can open, and the way to both places) and Portfolio tabs (one member or
// every member, View or Restricted, an optional end: PortfolioTabShares.tsx, kept in the
// portfolio). One share is one switch; a switch sends once however it is tapped (the section
// holds one write at a time) and shows the service's sentence.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ChevronRight, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { watchlistApi } from '@/api/watchlist'
import { Label } from '@/components/ui/label'
import { ownerApi } from '../ownerApi'
import type { MemberCard, Share, ShareKind } from '../types'
import { SHARES_KEY } from './keys'
import { PortfolioTabShares } from './PortfolioTabShares'
import { CARD, SaidLine, SwitchRow, useOnce } from './parts'
import { coveredByAll, rowFor, type Target } from './shareRules'
import { BOOK_SHARE_RULE } from './words'

const SELECT =
  'h-12 w-full rounded-md border border-[color:var(--input)] bg-card px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring'

function Note({ children }: { children: string }) {
  return <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">{children}</p>
}

/** One switch for one share row: on makes it, off removes it, each at the row's own version. */
function useShareSwitch(shares: readonly Share[]) {
  const queryClient = useQueryClient()
  const once = useOnce((_said, error) => {
    if (error === null || error.unconfirmed || error.code === 'conflict') {
      void queryClient.invalidateQueries({ queryKey: SHARES_KEY })
    }
  })
  const flip = (kind: ShareKind, ref: string, target: Target, on: boolean, label: string) => {
    const row = rowFor(shares, kind, ref, target)
    if (on) {
      if (row) return
      void once
        .run(
          () =>
            ownerApi.shareSet({
              kind,
              ref,
              member_id: target === 'all' ? null : target,
              role: null,
              level: 'view',
              expires_at: null,
            }),
          () => `${label} is shared.`
        )
        .then((made) => {
          if (made) void queryClient.invalidateQueries({ queryKey: SHARES_KEY })
        })
    } else if (row) {
      void once
        .run(
          () => ownerApi.shareRemove({ id: row.id, version: row.version }),
          (done) => done.words ?? `${label} is no longer shared.`
        )
        .then((done) => {
          if (done) void queryClient.invalidateQueries({ queryKey: SHARES_KEY })
        })
    }
  }
  return { ...once, flip }
}

function TargetPicker({
  id,
  members,
  value,
  onChange,
  every,
}: {
  id: string
  members: MemberCard[]
  value: Target | null
  onChange: (next: Target) => void
  every: boolean
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>Share with</Label>
      <select
        id={id}
        className={SELECT}
        value={value === null ? '' : String(value)}
        onChange={(e) => {
          if (e.target.value === '') return
          onChange(e.target.value === 'all' ? 'all' : Number(e.target.value))
        }}
      >
        {value === null ? (
          <option value="" disabled>
            {CHOOSE_TARGET}
          </option>
        ) : null}
        {members.map((m) => (
          <option key={m.id} value={m.id}>
            {m.display_name}
          </option>
        ))}
        {every ? <option value="all">Every member</option> : null}
      </select>
    </div>
  )
}

export const CHOOSE_TARGET = 'Choose who to share with'
export const CHOOSE_FIRST = 'Choose who to share with above. The switches appear after that.'
export const EVERY_NOTE =
  'Every member is chosen: a switch here shares the list with everyone. Tap the switch itself; the rest of the row does nothing.'

/** What a list's row says before anyone is chosen: who has it now. */
function sharedWith(shares: readonly Share[], ref: string): string {
  if (coveredByAll(shares, 'watchlist', ref)) return 'Shared with every member'
  const count = shares.filter(
    (s) => s.kind === 'watchlist' && s.ref === ref && s.member_id !== null
  ).length
  if (count === 0) return 'Not shared'
  return count === 1 ? 'Shared with 1 member' : `Shared with ${count} members`
}

function Watchlists({ members, shares }: { members: MemberCard[]; shares: readonly Share[] }) {
  // Verification N8 (v0.12.0): the picker stood at "Every member" and each row was its switch's
  // label, so one stray tap on a list's name shared it with everyone. Nobody is chosen at first
  // (the one member, where there is only one); "Every member" has to be picked, and then only the
  // switch itself takes a tap. A single member's rows still take a tap anywhere.
  const [target, setTarget] = useState<Target | null>(members.length === 1 ? members[0].id : null)
  const lists = useQuery({
    queryKey: ['sudarshan', 'family-owner', 'owner-lists'],
    queryFn: () => watchlistApi.list(),
    retry: false,
  })
  const swap = useShareSwitch(shares)

  return (
    <section aria-labelledby="share-lists" className={`${CARD} flex flex-col gap-3`}>
      <h2 id="share-lists" className="text-[1.0625rem] font-bold">
        Watchlists
      </h2>
      <Note>Members see the lists you share, with prices. They cannot change a list.</Note>
      <TargetPicker
        id="share-lists-target"
        members={members}
        value={target}
        onChange={setTarget}
        every
      />
      {target === null ? <Note>{CHOOSE_FIRST}</Note> : null}
      {target === 'all' ? <Note>{EVERY_NOTE}</Note> : null}
      <SaidLine said={swap.said} />
      {lists.isPending ? (
        <p className="flex items-center gap-2 text-[0.9375rem]">
          <Loader2 aria-hidden="true" className="size-4 animate-spin" />
          Reading your lists.
        </p>
      ) : lists.isError ? (
        <p className="text-[0.9375rem] text-[color:var(--warning-text)]">
          Your watchlists could not be read just now. Open this page again in a minute.
        </p>
      ) : lists.data.length === 0 ? (
        <p className="text-[0.9375rem]">You have no watchlist to share yet.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
          {lists.data.map((list) => {
            const ref = String(list.id)
            const count = `${list.items.length} ${list.items.length === 1 ? 'symbol' : 'symbols'}`
            if (target === null) {
              // nobody chosen yet: who has the list now, and no switch to flip by mistake
              return (
                <li key={list.id} className="flex min-h-12 flex-col justify-center gap-1 py-1.5">
                  <span className="truncate text-sm leading-none font-medium">{list.name}</span>
                  <span className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
                    {`${count} · ${sharedWith(shares, ref)}`}
                  </span>
                </li>
              )
            }
            const own = rowFor(shares, 'watchlist', ref, target) !== undefined
            const viaAll = target !== 'all' && coveredByAll(shares, 'watchlist', ref)
            const on = own || viaAll
            return (
              <li key={list.id}>
                <SwitchRow
                  id={`share-list-${list.id}`}
                  name={list.name}
                  line={
                    viaAll && !own
                      ? 'Shared with every member'
                      : `${count} · ${on ? 'Shared' : 'Not shared'}${target === 'all' ? ' with every member' : ''}`
                  }
                  label={
                    target === 'all' ? `Share ${list.name} with every member` : `Share ${list.name}`
                  }
                  checked={on}
                  disabled={swap.pending || (viaAll && !own)}
                  switchOnly={target === 'all'}
                  onCheckedChange={(next) => swap.flip('watchlist', ref, target, next, list.name)}
                />
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

/** The line that stands where the book switches were: the server's own sentence. */
export const BOOKS_RULE = BOOK_SHARE_RULE

const LINK =
  'flex min-h-12 w-full items-center justify-between gap-3 rounded-xl border px-3 text-left text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring'

const shownWord = (on: boolean) => (on ? 'shown' : 'hidden')

/**
 * Review H1 (v0.12.0): which books a member can open is decided by the Upstox tab's share and by
 * the member's Orders and Funds switches, and by nothing else. This section had a switch for each
 * book that the server kept and never read; it now says what decides it and leads to both places.
 */
function Books({
  members,
  onOpenMember,
}: {
  members: MemberCard[]
  onOpenMember?: (id: number) => void
}) {
  const toTabs = () => {
    const heading = document.getElementById('share-tabs')
    heading?.scrollIntoView?.({ block: 'start' })
    heading?.focus?.()
  }
  return (
    <section aria-labelledby="share-books" className={`${CARD} flex flex-col gap-3`}>
      <h2 id="share-books" className="text-[1.0625rem] font-bold">
        Books
      </h2>
      <p className="text-[0.9375rem] leading-snug">{BOOKS_RULE}</p>
      <button type="button" className={LINK} onClick={toTabs}>
        <span>Share the Upstox tab</span>
        <span className="flex shrink-0 items-center gap-1 text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
          Portfolio tabs
          <ArrowDown aria-hidden="true" className="size-4" />
        </span>
      </button>
      {members.length === 0 ? (
        <p className="text-[0.9375rem]">Add a member first.</p>
      ) : (
        <ul aria-label="Orders and Funds switches" className="flex flex-col gap-2">
          {members.map((member) => {
            const state = `Orders and trades ${shownWord(member.privacy.orders)} · Funds ${shownWord(member.privacy.funds)}`
            const body = (
              <span className="flex min-w-0 flex-col py-2">
                <span className="truncate">{member.display_name}</span>
                <span className="text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
                  {state}
                </span>
              </span>
            )
            return (
              <li key={member.id}>
                {onOpenMember ? (
                  <button
                    type="button"
                    className={LINK}
                    aria-label={`Privacy settings of ${member.display_name}: ${state}`}
                    onClick={() => onOpenMember(member.id)}
                  >
                    {body}
                    <ChevronRight aria-hidden="true" className="size-4 shrink-0" />
                  </button>
                ) : (
                  <div className={LINK.replace(' hover:bg-accent', '')}>{body}</div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

export function Shares({
  members,
  now = Date.now(),
  onOpenMember,
}: {
  members: MemberCard[]
  now?: number
  /** Opens a member's sheet, where the Orders and Funds switches are. */
  onOpenMember?: (id: number) => void
}) {
  const shares = useQuery({
    queryKey: SHARES_KEY,
    queryFn: ({ signal }) => ownerApi.shareList(signal),
    refetchOnWindowFocus: true,
    retry: false,
  })
  if (shares.isPending) {
    return (
      <output className={`${CARD} flex items-center gap-2 text-[0.9375rem]`}>
        <Loader2 aria-hidden="true" className="size-4 animate-spin" />
        Reading what is shared.
      </output>
    )
  }
  if (shares.isError) {
    return (
      <div role="alert" className={`${CARD} flex flex-col gap-3`}>
        <p className="text-[0.9375rem]">{shares.error.message}</p>
      </div>
    )
  }
  const live = members.filter((m) => m.status !== 'removed')
  return (
    <div className="flex flex-col gap-4">
      <Watchlists members={live} shares={shares.data} />
      <Books members={live} onOpenMember={onOpenMember} />
      <PortfolioTabShares members={live} now={now} />
    </div>
  )
}
