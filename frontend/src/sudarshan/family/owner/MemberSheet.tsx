// SUDARSHAN P6 D7a (M20.6): the sheet of one member, or of a member about to be added. Edit (name,
// role, privacy, language), Invite or Reset sign-in, Unlock sign-in while wrong attempts hold it
// (review L1), Suspend or Resume, Devices (each with Sign out) and Remove. Every write goes
// through one lock (`useOnce`): it sends once however it is tapped, carries the card's `version`
// (or a `request_id` for a create, an invite, a reset), and shows the answer's sentence. A
// destructive action asks first. A write that was not answered says "Not confirmed" and the page
// reads the members again; it never says "failed".
//
// M2.8 (cross-design Y6): a privacy or role change also writes the portfolio's floor for the
// member, through the portfolio bridge, in the order of privacySave.ts; the sheet says "Saved"
// only when both are confirmed, and says so when the portfolio's floor is behind the card.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CirclePause,
  KeyRound,
  Loader2,
  LockOpen,
  Play,
  Smartphone,
  Trash2,
  TriangleAlert,
  UserPlus,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { SharesList } from '@/sudarshan/portfolio/types'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { newRequestId, OwnerError, ownerApi, ownerProblem } from '../ownerApi'
import {
  CATEGORIES,
  type Invite,
  type Language,
  LIMITS,
  type MemberCard,
  PRIVACY_DEFAULTS,
  type Privacy,
  type Role,
} from '../types'
import { devicesKey, MEMBERS_KEY, PORTFOLIO_SHARES_KEY } from './keys'
import { Ask, SaidLine, SwitchRow, useOnce } from './parts'
import {
  driftOf,
  type FloorDrift,
  floorOf,
  fromPortfolio,
  memberKey,
  portfolioShares,
} from './portfolioShares'
import { listOf, planPrivacy, runPrivacySave } from './privacySave'
import {
  CATEGORY_LABEL,
  FUNDS_WITHOUT_AMOUNTS,
  PNL_HIDDEN_NOTE,
  PNL_NEEDS_QTY,
  ROLE_WORDS,
  SENT_OK,
  settled,
  whenOf,
} from './words'

export const SUSPEND_ASK =
  'Suspend this member? Every device of theirs is signed out at once, and they cannot sign in until you resume them.'
export const REMOVE_ASK =
  'Remove this member? Their devices are signed out and their Telegram unlinked. The audit record stays.'
export const RESET_ASK =
  'Reset sign-in? Their devices are signed out, and their password and code app stop working until they use the new invite link.'
export const SIGN_OUT_ASK = 'Sign out this device? They can sign in again on it.'
/** Review L1: what Unlock sign-in does, said under its button while a lock is on. */
export const UNLOCK_LINE =
  'Wrong attempts paused sign-ins on phones this member has not used. Unlock lets them try again now; their password and code app stay as they are.'
export const UNLOCKED = 'Unlocked. They can sign in again now.'

/** The portfolio's floor of a member, against the card (cross-design Y6). */
export const FLOOR_MISSING =
  'The portfolio does not have their privacy yet, so it shows them no figures and no tab shared with every member. Save to send it.'
export const FLOOR_FOLLOW = 'Save to make it follow these switches.'
export const FLOOR_UNREAD =
  'Their privacy in the portfolio is still being read, so nothing was saved. Try again in a moment.'
export const FLOOR_WAITS =
  'Their privacy is kept in the portfolio too, so it cannot be changed until the portfolio answers.'
export const MADE_NO_FLOOR =
  'Member added. Their privacy did not reach the portfolio, so it shows them no figures until you save the member again.'
export const MADE_FLOOR_UNSURE =
  'Member added; whether their privacy reached the portfolio is not confirmed. Open the member to check.'

/** What the sheet says of a floor that is not the card's, or null when they agree. */
export function floorNotice(drift: FloorDrift | null): string | null {
  if (drift === null || drift.kind === 'same') return null
  if (drift.kind === 'missing') return FLOOR_MISSING
  const hides = CATEGORIES.filter((c) => drift.hidesMore[c])
  const shows = CATEGORIES.filter((c) => drift.showsMore[c])
  const parts = [
    hides.length ? `still hides ${listOf(hides)} from them` : null,
    shows.length ? `shows them ${listOf(shows)}, which these switches hide` : null,
  ].filter(Boolean)
  if (parts.length === 0) return `The portfolio has another role for them. ${FLOOR_FOLLOW}`
  return `The portfolio ${parts.join(' and ')}. ${FLOOR_FOLLOW}`
}

function FloorNotice({ text }: { text: string | null }) {
  if (!text) return null
  return (
    <p className="flex items-start gap-2 rounded-xl bg-[color:var(--gold-tint)] px-3 py-2 text-[0.875rem] leading-snug text-[color:var(--warning-text)]">
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>{text}</span>
    </p>
  )
}

const LANGUAGES: ReadonlyArray<{ value: Language; label: string }> = [
  { value: 'en', label: 'English' },
  { value: 'gu', label: 'ગુજરાતી' },
]

type Asking =
  | { kind: 'remove' }
  | { kind: 'reset' }
  | { kind: 'suspend' }
  | { kind: 'device'; id: string }
  | null

function sameRole(a: Role): Privacy {
  return { ...PRIVACY_DEFAULTS[a] }
}

/** What a category's switch leaves the member, in the owner's words (Y24, H1). */
function categoryLine(category: (typeof CATEGORIES)[number], privacy: Privacy): string {
  if (category === 'pnl' && !privacy.qty) return PNL_NEEDS_QTY
  if (category === 'pnl' && !privacy.pnl) return `Hidden. ${PNL_HIDDEN_NOTE}`
  if (category === 'funds' && privacy.funds && !privacy.pnl) return FUNDS_WITHOUT_AMOUNTS
  return privacy[category] ? 'Shown' : 'Hidden'
}

/** The fields of the edit form: for a new member (card undefined) or an existing one. */
function Fields({
  card,
  busy,
  behind = false,
  onSave,
  saveLabel,
}: {
  card: MemberCard | undefined
  busy: boolean
  /** The portfolio's floor is not the card's: a save with nothing changed here sends it. */
  behind?: boolean
  onSave: (fields: {
    display_name: string
    role: Role
    privacy: Privacy
    language: Language
  }) => void
  saveLabel: string
}) {
  const [name, setName] = useState(card?.display_name ?? '')
  const [role, setRole] = useState<Role>(card?.role ?? 'viewer')
  const [privacy, setPrivacy] = useState<Privacy>(card?.privacy ?? sameRole('viewer'))
  const [language, setLanguage] = useState<Language>(card?.language ?? 'en')
  const touched = useRef(false)

  const pickRole = (next: Role) => {
    setRole(next)
    // A role's defaults are only a starting point: once the owner has chosen, they stay.
    if (!touched.current) setPrivacy(sameRole(next))
  }
  const flip = (category: (typeof CATEGORIES)[number], on: boolean) => {
    touched.current = true
    setPrivacy((now_) => settled({ ...now_, [category]: on }))
  }
  const effective = settled(privacy)
  const clean = name.trim()
  const dirty =
    !card ||
    behind ||
    clean !== card.display_name ||
    role !== card.role ||
    language !== card.language ||
    CATEGORIES.some((c) => effective[c] !== card.privacy[c])

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (clean) onSave({ display_name: clean, role, privacy: effective, language })
      }}
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="member-name">Display name</Label>
        <Input
          id="member-name"
          className="h-12"
          value={name}
          maxLength={LIMITS.display_name_max}
          onChange={(event) => setName(event.target.value)}
          autoComplete="off"
        />
        <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
          Kept on the server only. It never goes into a notice.
        </p>
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-semibold">Role</legend>
        {(['viewer', 'child'] as const).map((value) => (
          <label
            key={value}
            className="flex min-h-12 cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 has-[:checked]:border-[color:var(--brand)] has-[:checked]:bg-[color:var(--brand-tint)]"
          >
            <input
              type="radio"
              name="member-role"
              className="mt-1 size-5 accent-[color:var(--brand)]"
              checked={role === value}
              onChange={() => pickRole(value)}
            />
            <span className="flex flex-col">
              <span className="font-semibold">{ROLE_WORDS[value].label}</span>
              <span className="text-[0.8125rem] text-[color:var(--text-secondary)]">
                {ROLE_WORDS[value].line}
              </span>
            </span>
          </label>
        ))}
      </fieldset>

      <fieldset className="flex flex-col">
        <legend className="mb-1 text-sm font-semibold">What this member sees</legend>
        {CATEGORIES.map((category) => {
          const needsQty = category === 'pnl' && !effective.qty
          return (
            <SwitchRow
              key={category}
              id={`privacy-${category}`}
              name={CATEGORY_LABEL[category].name}
              line={categoryLine(category, effective)}
              checked={effective[category]}
              disabled={needsQty}
              onCheckedChange={(on) => flip(category, on)}
            />
          )
        })}
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-semibold">
          Language for Telegram and notifications
        </legend>
        <div className="flex gap-2">
          {LANGUAGES.map((option) => (
            <label
              key={option.value}
              className="flex min-h-12 flex-1 cursor-pointer items-center justify-center rounded-xl border px-3 font-semibold has-[:checked]:border-[color:var(--brand)] has-[:checked]:bg-[color:var(--brand-tint)]"
            >
              <input
                type="radio"
                name="member-language"
                className="sr-only"
                checked={language === option.value}
                onChange={() => setLanguage(option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
      </fieldset>

      <Button type="submit" className="h-12" disabled={busy || !clean || !dirty}>
        {busy ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : null}
        {saveLabel}
      </Button>
    </form>
  )
}

function Devices({
  card,
  now,
  onSignOut,
  pending,
  asking,
  setAsking,
}: {
  card: MemberCard
  now: number
  onSignOut: (id: string) => void
  pending: boolean
  asking: Asking
  setAsking: (next: Asking) => void
}) {
  const devices = useQuery({
    queryKey: devicesKey(card.id),
    queryFn: ({ signal }) => ownerApi.deviceList(card.id, signal),
  })
  return (
    <section aria-labelledby="member-devices" className="flex flex-col gap-2">
      <h3 id="member-devices" className="text-sm font-semibold">
        Devices
      </h3>
      {devices.isPending ? (
        <p className="text-[0.9375rem] text-[color:var(--text-secondary)]">Reading devices.</p>
      ) : devices.isError ? (
        <p className="text-[0.9375rem] text-[color:var(--warning-text)]">{devices.error.message}</p>
      ) : devices.data.length === 0 ? (
        <p className="text-[0.9375rem] text-[color:var(--text-secondary)]">
          No device is signed in.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
          {devices.data.map((device) => (
            <li key={device.id} className="flex flex-col gap-2 py-2">
              <div className="flex items-center gap-3">
                <Smartphone
                  aria-hidden="true"
                  className="size-5 shrink-0 text-[color:var(--text-secondary)]"
                />
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-semibold">{device.label}</span>
                  <span className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
                    {`Signed in ${whenOf(device.created_at, now)} · last used ${whenOf(device.last_seen_at, now)}`}
                  </span>
                  {device.notifications ? (
                    <span className="text-[0.8125rem] text-[color:var(--warning-text)]">
                      {device.notifications}
                    </span>
                  ) : null}
                </div>
                <Button
                  type="button"
                  variant="outline"
                  className="h-12"
                  aria-label={`Sign out ${device.label}`}
                  onClick={() => setAsking({ kind: 'device', id: device.id })}
                >
                  Sign out
                </Button>
              </div>
              {asking?.kind === 'device' && asking.id === device.id ? (
                <Ask
                  question={SIGN_OUT_ASK}
                  yes="Sign out"
                  pending={pending}
                  onYes={() => onSignOut(device.id)}
                  onNo={() => setAsking(null)}
                />
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export function MemberSheet({
  open,
  onOpenChange,
  card,
  now,
  onInvite,
  onCreated,
  onGone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The member to manage; undefined is the sheet that adds one. */
  card: MemberCard | undefined
  now: number
  /** An invite was made: the link is shown once, by the page. */
  onInvite: (invite: Invite, name: string) => void
  /** A member was added: the page opens that member's sheet. */
  onCreated: (card: MemberCard) => void
  onGone: () => void
}) {
  const queryClient = useQueryClient()
  const [asking, setAsking] = useState<Asking>(null)
  // One id per attempt at a write that takes one, kept until a definite answer, so a second tap
  // after "not confirmed" repeats the same request and the service does not do it twice.
  const ids = useRef(new Map<string, string>())
  const idFor = (key: string) => {
    let id = ids.current.get(key)
    if (!id) {
      id = newRequestId()
      ids.current.set(key, id)
    }
    return id
  }
  // A definite answer (done, or refused in words) ends the attempt: the next tap is a new one.
  // No answer (unconfirmed) keeps it, so the repeat is the same request.
  const attempt = async <T,>(key: string, send: () => Promise<T>): Promise<T> => {
    try {
      const done = await send()
      ids.current.delete(key)
      return done
    } catch (caught) {
      if (!ownerProblem(caught).unconfirmed) ids.current.delete(key)
      throw caught
    }
  }
  const reread = () => queryClient.invalidateQueries({ queryKey: MEMBERS_KEY })
  const rereadFloors = () => queryClient.invalidateQueries({ queryKey: PORTFOLIO_SHARES_KEY })
  const once = useOnce((_said, error) => {
    if (error === null || error.unconfirmed || error.code === 'conflict') void reread()
  })
  // The portfolio's floors (and tab shares), read through the portfolio bridge (Y6).
  const floors = useQuery({
    queryKey: PORTFOLIO_SHARES_KEY,
    queryFn: ({ signal }) => portfolioShares.list(signal),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: true,
  })
  const drift = card && floors.data ? driftOf(floorOf(floors.data, card.id), card) : null
  const behind = drift !== null && drift.kind !== 'same'
  const floorProblem = floors.error ? fromPortfolio(floors.error) : null
  const floorWords = floorProblem ? `${floorProblem.message} ${FLOOR_WAITS}` : floorNotice(drift)

  // biome-ignore lint/correctness/useExhaustiveDependencies: a different member starts clean
  useEffect(() => {
    setAsking(null)
    once.clear()
  }, [card?.id, open])

  const id = card?.id
  const version = card?.version

  const save = (fields: {
    display_name: string
    role: Role
    privacy: Privacy
    language: Language
  }) => {
    if (!card) {
      void once
        .run(
          async () => {
            const made = await attempt('create', () =>
              ownerApi.memberCreate({ ...fields, request_id: idFor('create') })
            )
            // The floor goes after the member exists: it is named by the new id, and a floor
            // where there was none can only show more (privacySave.ts, step 3).
            let problem: OwnerError | null = null
            try {
              const known = queryClient.getQueryData<SharesList>(PORTFOLIO_SHARES_KEY)
              const stale = floorOf(known, made.id)
              await portfolioShares.floor({
                member: memberKey(made.id),
                role: made.role,
                shown: made.privacy,
                version: stale?.version ?? null,
              })
            } catch (caught) {
              problem = fromPortfolio(caught)
            }
            void rereadFloors()
            return { made, problem }
          },
          ({ problem }) =>
            problem === null
              ? 'Member added. Make an invite link next.'
              : problem.unconfirmed
                ? { kind: 'unconfirmed', text: MADE_FLOOR_UNSURE }
                : { kind: 'problem', text: `${MADE_NO_FLOOR} ${problem.message}` }
        )
        .then((result) => {
          if (!result) return
          void reread()
          onCreated(result.made)
        })
      return
    }
    const changed: Record<string, unknown> = {}
    if (fields.display_name !== card.display_name) changed.display_name = fields.display_name
    if (fields.role !== card.role) changed.role = fields.role
    if (fields.language !== card.language) changed.language = fields.language
    if (CATEGORIES.some((c) => fields.privacy[c] !== card.privacy[c]))
      changed.privacy = fields.privacy
    if (!('privacy' in changed) && !('role' in changed) && !behind) {
      // Nothing the portfolio keeps: the family service's row alone.
      void once
        .run(
          () => ownerApi.memberUpdate({ id: card.id, version: card.version, ...changed }),
          () => 'Saved.'
        )
        .then((saved) => {
          if (saved) void reread()
        })
      return
    }
    void once.run(
      async () => {
        if (!floors.data) {
          throw floorProblem
            ? new OwnerError(floorProblem.code, `${floorProblem.message} ${FLOOR_WAITS}`)
            : new OwnerError('unavailable', FLOOR_UNREAD)
        }
        const row = floorOf(floors.data, card.id)
        const start = row ? { shown: row.shown, role: row.role } : null
        const steps = planPrivacy({
          floor: start,
          next: { privacy: fields.privacy, role: fields.role },
          familyChanged: Object.keys(changed).length > 0,
        })
        try {
          await runPrivacySave(
            steps,
            {
              family: () =>
                ownerApi.memberUpdate({ id: card.id, version: card.version, ...changed }),
              floor: (plan, version) =>
                portfolioShares.floor({
                  member: memberKey(card.id),
                  role: plan.role,
                  shown: plan.shown,
                  version,
                }),
            },
            { floor: start, version: row?.version ?? null }
          )
        } finally {
          void reread()
          void rereadFloors()
        }
        return true
      },
      () => 'Saved.'
    )
  }

  const invite = (kind: 'invite' | 'reset') => {
    if (!card) return
    const key = `${kind}:${card.id}`
    void once
      .run(
        () =>
          attempt(key, () =>
            kind === 'invite'
              ? ownerApi.inviteCreate({ member_id: card.id, request_id: idFor(key) })
              : ownerApi.signinReset({
                  member_id: card.id,
                  version: card.version,
                  request_id: idFor(key),
                })
          ),
        () => 'Invite link made.'
      )
      .then((made) => {
        if (!made) return
        setAsking(null)
        void reread()
        onInvite(made, card.display_name)
      })
  }

  const unlock = () => {
    if (!card) return
    void once
      .run(
        () => ownerApi.signinUnlock({ member_id: card.id }),
        () => UNLOCKED
      )
      .then((done) => {
        if (done) void reread()
      })
  }

  const revoke = () => {
    if (!card) return
    void once
      .run(
        () => ownerApi.inviteRevoke({ member_id: card.id }),
        () => 'The invite link no longer works.'
      )
      .then((done) => {
        if (done) void reread()
      })
  }

  const suspend = (status: 'active' | 'suspended') => {
    if (!card) return
    void once
      .run(
        () => ownerApi.memberStatus({ id: card.id, version: card.version, status }),
        () => (status === 'suspended' ? 'Suspended. They are signed out.' : 'Resumed.')
      )
      .then((done) => {
        setAsking(null)
        if (done) void reread()
      })
  }

  const remove = () => {
    if (!card) return
    void once
      .run(
        () => ownerApi.memberRemove({ id: card.id, version: card.version }),
        (done) => done.words ?? SENT_OK
      )
      .then((done) => {
        if (!done) return
        setAsking(null)
        void reread()
        onGone()
      })
  }

  const signOut = (deviceId: string) => {
    if (!card) return
    void once
      .run(
        () => ownerApi.deviceRevoke({ member_id: card.id, id: deviceId }),
        (done) => done.words ?? 'Signed out on that device.'
      )
      .then((done) => {
        if (!done) return
        setAsking(null)
        void queryClient.invalidateQueries({ queryKey: devicesKey(card.id) })
        void reread()
      })
  }

  const status = card?.status
  const joined = card?.handle != null
  const locked = card?.locked_until != null && card.locked_until > now
  const fieldsKey = useMemo(() => `${id ?? 'new'}:${version ?? 0}`, [id, version])

  return (
    <BottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title={card ? card.display_name : 'Add a member'}
      description={card ? 'Manage this member' : 'Up to 4 members can see the family view'}
    >
      <div className="flex flex-col gap-5">
        <SaidLine said={once.said} />
        {card ? <FloorNotice text={floorWords} /> : null}
        <Fields
          key={fieldsKey}
          card={card}
          busy={once.pending}
          behind={behind}
          onSave={save}
          saveLabel={card ? 'Save changes' : 'Add member'}
        />

        {card ? (
          <>
            <section aria-labelledby="member-signin" className="flex flex-col gap-2">
              <h3 id="member-signin" className="text-sm font-semibold">
                Sign-in
              </h3>
              {joined ? (
                <>
                  {locked ? (
                    <>
                      <Button
                        type="button"
                        className="h-12 justify-start"
                        disabled={once.pending}
                        onClick={unlock}
                      >
                        <LockOpen aria-hidden="true" className="size-4" />
                        Unlock sign-in
                      </Button>
                      <p className="text-[0.8125rem] text-[color:var(--text-secondary)]">
                        {UNLOCK_LINE}
                      </p>
                    </>
                  ) : null}
                  <Button
                    type="button"
                    variant="outline"
                    className="h-12 justify-start"
                    disabled={once.pending}
                    onClick={() => setAsking({ kind: 'reset' })}
                  >
                    <KeyRound aria-hidden="true" className="size-4" />
                    Reset sign-in
                  </Button>
                  {asking?.kind === 'reset' ? (
                    <Ask
                      question={RESET_ASK}
                      yes="Reset sign-in"
                      pending={once.pending}
                      onYes={() => invite('reset')}
                      onNo={() => setAsking(null)}
                    />
                  ) : null}
                </>
              ) : (
                <>
                  <Button
                    type="button"
                    className="h-12 justify-start"
                    disabled={once.pending || status === 'suspended'}
                    onClick={() => invite('invite')}
                  >
                    <UserPlus aria-hidden="true" className="size-4" />
                    Invite
                  </Button>
                  {status === 'invited' ? (
                    <Button
                      type="button"
                      variant="outline"
                      className="h-12 justify-start"
                      disabled={once.pending}
                      onClick={revoke}
                    >
                      Cancel the open invite
                    </Button>
                  ) : null}
                </>
              )}
            </section>

            {status === 'suspended' || status === 'active' ? (
              <section aria-labelledby="member-status" className="flex flex-col gap-2">
                <h3 id="member-status" className="text-sm font-semibold">
                  Access
                </h3>
                {status === 'suspended' ? (
                  <Button
                    type="button"
                    variant="outline"
                    className="h-12 justify-start"
                    disabled={once.pending}
                    onClick={() => suspend('active')}
                  >
                    <Play aria-hidden="true" className="size-4" />
                    Resume
                  </Button>
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    className="h-12 justify-start"
                    disabled={once.pending}
                    onClick={() => setAsking({ kind: 'suspend' })}
                  >
                    <CirclePause aria-hidden="true" className="size-4" />
                    Suspend
                  </Button>
                )}
                {/* Review L1 (v0.12.0): it signs the member out of every device, so it asks. */}
                {status === 'active' && asking?.kind === 'suspend' ? (
                  <Ask
                    question={SUSPEND_ASK}
                    yes="Suspend"
                    pending={once.pending}
                    onYes={() => suspend('suspended')}
                    onNo={() => setAsking(null)}
                  />
                ) : null}
              </section>
            ) : null}

            <Devices
              card={card}
              now={now}
              onSignOut={signOut}
              pending={once.pending}
              asking={asking}
              setAsking={setAsking}
            />

            <section aria-labelledby="member-remove" className="flex flex-col gap-2">
              <h3 id="member-remove" className="text-sm font-semibold">
                Remove
              </h3>
              <Button
                type="button"
                variant="outline"
                className="h-12 justify-start text-[color:var(--down)]"
                disabled={once.pending}
                onClick={() => setAsking({ kind: 'remove' })}
              >
                <Trash2 aria-hidden="true" className="size-4" />
                Remove this member
              </Button>
              {asking?.kind === 'remove' ? (
                <Ask
                  question={REMOVE_ASK}
                  yes="Remove member"
                  pending={once.pending}
                  onYes={remove}
                  onNo={() => setAsking(null)}
                />
              ) : null}
            </section>
          </>
        ) : null}
      </div>
    </BottomSheet>
  )
}
