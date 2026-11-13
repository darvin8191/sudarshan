// SUDARSHAN P6 D7a (M20.6): the owner's Family page. The family members, their invites, devices
// and privacy. Everything here is read through OpenAlgo's owner bridge (family/ownerApi.ts); the
// page asks the service for nothing else. Members are read when the page opens, when it gets
// focus and every 30 seconds while it is open (this is not market data, rule 7 allows it).
import { useQuery } from '@tanstack/react-query'
import { Loader2, UserPlus, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { InviteSheet } from '@/sudarshan/family/owner/InviteSheet'
import { MEMBERS_KEY } from '@/sudarshan/family/owner/keys'
import { MemberCard } from '@/sudarshan/family/owner/MemberCard'
import { MemberSheet } from '@/sudarshan/family/owner/MemberSheet'
import { CARD } from '@/sudarshan/family/owner/parts'
import { OwnerError, ownerApi } from '@/sudarshan/family/ownerApi'
import { type Invite, LIMITS } from '@/sudarshan/family/types'

const REFRESH_MS = 30_000

/** The page's clock: moved by the timer, so a card's "expires 18:40" ends on time. */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const tick = () => setNow(Date.now())
    const id = window.setInterval(tick, 5_000)
    window.addEventListener('focus', tick)
    return () => {
      window.clearInterval(id)
      window.removeEventListener('focus', tick)
    }
  }, [])
  return now
}

export default function FamilyMembers() {
  const now = useNow()
  const members = useQuery({
    queryKey: MEMBERS_KEY,
    queryFn: ({ signal }) => ownerApi.memberList(signal),
    refetchInterval: REFRESH_MS,
    refetchOnWindowFocus: true,
    staleTime: 0,
    retry: false,
  })
  const [selected, setSelected] = useState<number | 'new' | null>(null)
  const [invite, setInvite] = useState<{ invite: Invite; name: string } | null>(null)

  const cards = (members.data ?? []).filter((card) => card.status !== 'removed')
  const card = typeof selected === 'number' ? cards.find((c) => c.id === selected) : undefined
  const full = cards.length >= LIMITS.max_members
  const off = members.error instanceof OwnerError && members.error.code === 'off'

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="sd-display text-[1.5rem] font-bold">Family</h1>
        <p className="text-sm text-[color:var(--text-secondary)]">
          Family members see your positions, holdings and the lists you share. They cannot place,
          change or cancel an order.
        </p>
      </header>

      {members.isPending ? (
        <output className={`block ${CARD} flex items-center gap-2 text-[0.9375rem]`}>
          <Loader2 aria-hidden="true" className="size-4 animate-spin" />
          Reading the family.
        </output>
      ) : off ? (
        <output className={`block ${CARD} text-[0.9375rem]`}>{members.error?.message}</output>
      ) : members.isError && !members.data ? (
        <div className={`${CARD} flex flex-col gap-3`} role="alert">
          <p className="text-[0.9375rem]">{members.error.message}</p>
          <Button type="button" className="h-12" onClick={() => members.refetch()}>
            Try again
          </Button>
        </div>
      ) : (
        <>
          {members.isError ? (
            <output className="block text-sm text-[color:var(--warning-text)]">
              {`${members.error.message} The list below is from the last time it was read.`}
            </output>
          ) : null}
          {cards.length === 0 ? (
            <div className={`${CARD} flex items-start gap-3`}>
              <Users
                aria-hidden="true"
                className="mt-0.5 size-5 shrink-0 text-[color:var(--text-secondary)]"
              />
              <p className="text-[0.9375rem]">
                No family member yet. Add one, then make an invite link and send it yourself.
              </p>
            </div>
          ) : (
            <ul className="flex flex-col gap-3">
              {cards.map((c) => (
                <li key={c.id}>
                  <MemberCard card={c} now={now} onOpen={setSelected} />
                </li>
              ))}
            </ul>
          )}
          <Button
            type="button"
            className="h-12 w-full"
            disabled={full}
            onClick={() => setSelected('new')}
          >
            <UserPlus aria-hidden="true" className="size-4" />
            Add member
          </Button>
          {full ? (
            <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
              {`The family view holds up to ${LIMITS.max_members} members. Remove one to add another.`}
            </p>
          ) : null}
        </>
      )}

      <MemberSheet
        open={selected !== null && invite === null && (selected === 'new' || card !== undefined)}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
        card={card}
        now={now}
        onInvite={(made, name) => setInvite({ invite: made, name })}
        onCreated={(made) => setSelected(made.id)}
        onGone={() => setSelected(null)}
      />
      <InviteSheet
        invite={invite?.invite ?? null}
        name={invite?.name ?? ''}
        now={now}
        onClose={() => setInvite(null)}
      />
    </div>
  )
}
