// SUDARSHAN P6 D7b (M8.15, M7.37): what the owner shares with the family. Watchlists (pick lists,
// for one member or every member), Books (positions, holdings, orders, trades and funds, one
// member at a time, beside what that member's privacy hides) and Portfolio tabs (one member or
// every member, View or Restricted, an optional end: PortfolioTabShares.tsx, kept in the
// portfolio). One share is one switch; a switch sends once however it is tapped (the section
// holds one write at a time) and shows the service's sentence.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { EyeOff, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { watchlistApi } from '@/api/watchlist'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { ownerApi } from '../ownerApi'
import type { MemberCard, Share, ShareKind } from '../types'
import { SHARES_KEY } from './keys'
import { PortfolioTabShares } from './PortfolioTabShares'
import { CARD, ROW, SaidLine, useOnce } from './parts'
import { BOOK_HIDDEN_BY, BOOK_PARTS, coveredByAll, rowFor, type Target } from './shareRules'

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
  value: Target
  onChange: (next: Target) => void
  every: boolean
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>Share with</Label>
      <select
        id={id}
        className={SELECT}
        value={String(value)}
        onChange={(e) => onChange(e.target.value === 'all' ? 'all' : Number(e.target.value))}
      >
        {every ? <option value="all">Every member</option> : null}
        {members.map((m) => (
          <option key={m.id} value={m.id}>
            {m.display_name}
          </option>
        ))}
      </select>
    </div>
  )
}

function Watchlists({ members, shares }: { members: MemberCard[]; shares: readonly Share[] }) {
  const [target, setTarget] = useState<Target>('all')
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
            const own = rowFor(shares, 'watchlist', ref, target) !== undefined
            const viaAll = target !== 'all' && coveredByAll(shares, 'watchlist', ref)
            const on = own || viaAll
            return (
              <li key={list.id} className={ROW}>
                <div className="flex min-w-0 flex-col">
                  <Label htmlFor={`share-list-${list.id}`} className="truncate">
                    {list.name}
                  </Label>
                  <span className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
                    {viaAll && !own
                      ? 'Shared with every member'
                      : `${list.items.length} ${list.items.length === 1 ? 'symbol' : 'symbols'} · ${on ? 'Shared' : 'Not shared'}`}
                  </span>
                </div>
                <Switch
                  id={`share-list-${list.id}`}
                  checked={on}
                  disabled={swap.pending || (viaAll && !own)}
                  aria-label={`Share ${list.name}`}
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

function Books({ members, shares }: { members: MemberCard[]; shares: readonly Share[] }) {
  const [id, setId] = useState<number | null>(members[0]?.id ?? null)
  const member = members.find((m) => m.id === id) ?? members[0]
  const swap = useShareSwitch(shares)
  return (
    <section aria-labelledby="share-books" className={`${CARD} flex flex-col gap-3`}>
      <h2 id="share-books" className="text-[1.0625rem] font-bold">
        Books
      </h2>
      <Note>
        Which of your books a member can open. What their privacy hides stays hidden even where a
        book is shared.
      </Note>
      {member ? (
        <>
          <TargetPicker
            id="share-books-target"
            members={members}
            value={member.id}
            onChange={(next) => setId(next === 'all' ? null : next)}
            every={false}
          />
          <SaidLine said={swap.said} />
          <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
            {BOOK_PARTS.map(({ part, label }) => {
              const on = rowFor(shares, 'book', part, member.id) !== undefined
              const category = BOOK_HIDDEN_BY[part as keyof typeof BOOK_HIDDEN_BY]
              const hidden = category !== undefined && !member.privacy[category]
              return (
                <li key={part} className={ROW}>
                  <div className="flex flex-col">
                    <Label htmlFor={`share-book-${part}`}>{label}</Label>
                    <span className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
                      {on ? 'Shared' : 'Not shared'}
                    </span>
                    {hidden ? (
                      <span className="flex items-center gap-1 text-[0.8125rem] text-[color:var(--warning-text)]">
                        <EyeOff aria-hidden="true" className="size-3.5" />
                        Hidden by their privacy settings
                      </span>
                    ) : null}
                  </div>
                  <Switch
                    id={`share-book-${part}`}
                    checked={on}
                    disabled={swap.pending}
                    aria-label={`Share ${label}`}
                    onCheckedChange={(next) => swap.flip('book', part, member.id, next, label)}
                  />
                </li>
              )
            })}
          </ul>
        </>
      ) : (
        <p className="text-[0.9375rem]">Add a member first.</p>
      )}
    </section>
  )
}

export function Shares({ members, now = Date.now() }: { members: MemberCard[]; now?: number }) {
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
      <Books members={live} shares={shares.data} />
      <PortfolioTabShares members={live} now={now} />
    </div>
  )
}
