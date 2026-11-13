// SUDARSHAN P6 D7b (M2.12): the audit record, newest first: the last 200 lines, filtered by member
// and by kind, with "Load older" for the page before. The server keeps it forever and refuses to
// change a line; this list only reads it. A line shows when, what, who it was about and on which
// device; the `detail` of a line has no shape in the contract, so it is not shown.
import { useInfiniteQuery } from '@tanstack/react-query'
import { History, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { ownerApi } from '../ownerApi'
import { type AuditKind, LIMITS, type MemberCard } from '../types'
import { AUDIT_KEY } from './keys'
import { CARD } from './parts'
import { AUDIT_WORDS, whenOf } from './words'

const SELECT =
  'h-12 w-full rounded-md border border-[color:var(--input)] bg-card px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring'
const KINDS = Object.keys(AUDIT_WORDS) as AuditKind[]

export function AuditList({ members, now }: { members: MemberCard[]; now: number }) {
  const [member, setMember] = useState<number | null>(null)
  const [kind, setKind] = useState<AuditKind | null>(null)
  const lines = useInfiniteQuery({
    queryKey: [...AUDIT_KEY, member, kind],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam, signal }) =>
      ownerApi.auditList(
        { member_id: member, kind, before: pageParam, limit: LIMITS.audit_page },
        signal
      ),
    getNextPageParam: (page) => page.older,
    refetchOnWindowFocus: true,
    retry: false,
  })
  const names = new Map(members.map((m) => [m.id, m.display_name]))
  const all = lines.data?.pages.flatMap((page) => page.lines) ?? []

  return (
    <section aria-labelledby="family-audit" className="flex flex-col gap-3">
      <div className={`${CARD} flex flex-col gap-3`}>
        <h2 id="family-audit" className="flex items-center gap-2 text-[1.0625rem] font-bold">
          <History aria-hidden="true" className="size-5" />
          Audit record
        </h2>
        <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
          {`The last ${LIMITS.audit_page} lines, newest first. A line is never changed or removed.`}
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="audit-member">Member</Label>
            <select
              id="audit-member"
              className={SELECT}
              value={member ?? ''}
              onChange={(e) => setMember(e.target.value === '' ? null : Number(e.target.value))}
            >
              <option value="">All members</option>
              {members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="audit-kind">Kind</Label>
            <select
              id="audit-kind"
              className={SELECT}
              value={kind ?? ''}
              onChange={(e) =>
                setKind(e.target.value === '' ? null : (e.target.value as AuditKind))
              }
            >
              <option value="">All kinds</option>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {AUDIT_WORDS[k]}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {lines.isPending ? (
        <output className={`${CARD} flex items-center gap-2 text-[0.9375rem]`}>
          <Loader2 aria-hidden="true" className="size-4 animate-spin" />
          Reading the record.
        </output>
      ) : lines.isError && all.length === 0 ? (
        <div role="alert" className={`${CARD} flex flex-col gap-3`}>
          <p className="text-[0.9375rem]">{lines.error.message}</p>
          <Button type="button" className="h-12" onClick={() => lines.refetch()}>
            Try again
          </Button>
        </div>
      ) : all.length === 0 ? (
        <p className={`${CARD} text-[0.9375rem]`}>No line matches.</p>
      ) : (
        <ul className={`${CARD} flex flex-col divide-y divide-[color:var(--divider)] py-1`}>
          {all.map((line) => (
            <li key={line.id} className="flex flex-col gap-0.5 py-2.5">
              <span className="text-[0.9375rem] font-semibold">{AUDIT_WORDS[line.kind]}</span>
              <span className="text-[0.8125rem] text-[color:var(--text-secondary)]">
                {[
                  whenOf(line.at, now),
                  line.member_id === null
                    ? null
                    : (names.get(line.member_id) ?? 'A member no longer listed'),
                  line.device,
                  line.place,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </li>
          ))}
        </ul>
      )}

      {lines.isError && all.length > 0 ? (
        <output className="block text-sm text-[color:var(--warning-text)]">
          {lines.error.message}
        </output>
      ) : null}
      {lines.hasNextPage ? (
        <Button
          type="button"
          variant="outline"
          className="h-12"
          disabled={lines.isFetchingNextPage}
          onClick={() => lines.fetchNextPage({ cancelRefetch: false })}
        >
          {lines.isFetchingNextPage ? (
            <Loader2 aria-hidden="true" className="size-4 animate-spin" />
          ) : null}
          Load older
        </Button>
      ) : null}
    </section>
  )
}
