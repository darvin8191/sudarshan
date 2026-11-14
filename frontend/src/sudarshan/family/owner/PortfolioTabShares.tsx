// SUDARSHAN M7.37: the Shares section's portfolio tabs. Each tab lists who it is shared with (every
// member, or one member), at which level (View, or Restricted: no figures) and until when; a tab is
// shared with one member or every member, changed, or stopped. The rows live in the portfolio and
// are written through OpenAlgo's portfolio bridge from the owner's browser (portfolioShares.ts).
// Every write sends once however it is tapped (the section holds one write at a time), with the
// version the portfolio asks for (none for a new share), and says the answer in words; "every
// member" is a share for each role, written one after another, and a write that stopped half way
// says which part took effect.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, Eye, FolderOpen, Loader2, Lock, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { portfolioCall } from '@/sudarshan/portfolio/api'
import type { ShareLevel, TabInfo, TabShare } from '@/sudarshan/portfolio/types'
import { OwnerError } from '../ownerApi'
import type { MemberCard, Role } from '../types'
import { PORTFOLIO_SHARES_KEY } from './keys'
import { CARD, Chip, ROW, SaidLine, useOnce } from './parts'
import { CHANGED_ELSEWHERE, floorOf, fromPortfolio, portfolioShares } from './portfolioShares'
import {
  fromIstInput,
  istInputValue,
  LEVEL_WORDS,
  linesFor,
  ROLES,
  roleViewer,
  rowsFor,
  type ShareLine,
  type ShareWho,
} from './tabShares'
import { ROLE_WORDS, whenOf } from './words'

export const TABS_NOTE =
  'A member sees a shared tab with what their privacy allows. Restricted shows the holdings by name only, with no figure.'
export const UPSTOX_NOTE =
  'The family view reads positions, holdings, orders and trades from this tab, so a member can open your books only while it is shared with them.'
export const ENDED_BLOCKS =
  'While this ended share stays, the share with every member does not reach them either.'
export const PICK_END = 'Choose when the share ends.'
export const END_AFTER_NOW = 'The share must end after now.'
const WEEK_MS = 7 * 86_400_000

const RADIO =
  'flex min-h-12 cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 has-[:checked]:border-[color:var(--brand)] has-[:checked]:bg-[color:var(--brand-tint)]'
const SELECT =
  'h-12 w-full rounded-md border border-[color:var(--input)] bg-card px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring'

const roleWords = (roles: readonly Role[]) =>
  roles.map((r) => `members with the ${ROLE_WORDS[r].label} role`).join(' and ')
const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

/** What a role-by-role write that stopped half way leaves, in one sentence and the reason. */
export function partialWords(
  tab: string,
  action: 'share' | 'stop',
  done: readonly Role[],
  failed: Role,
  rest: readonly Role[],
  error: OwnerError
): string {
  const again = 'The page reads it again.'
  const reason = error.code === 'conflict' ? CHANGED_ELSEWHERE : error.message
  if (action === 'share') {
    return error.unconfirmed
      ? `${tab} is now shared with ${roleWords(done)}; whether it is shared with ${roleWords([failed])} is not confirmed. ${again}`
      : `${tab} is now shared with ${roleWords(done)} only. ${reason}`
  }
  return error.unconfirmed
    ? `${capital(roleWords(done))} no longer see ${tab}; whether ${roleWords([failed])} still see it is not confirmed. ${again}`
    : `${capital(roleWords(done))} no longer see ${tab}; ${roleWords([failed, ...rest])} still do. ${reason}`
}

/** Writes one after another; the first that does not end well stops the rest and is said. */
async function byRole(
  tab: string,
  action: 'share' | 'stop',
  sends: ReadonlyArray<{ role: Role; send: () => Promise<unknown> }>
): Promise<void> {
  const done: Role[] = []
  for (const [index, { role, send }] of sends.entries()) {
    try {
      await send()
      done.push(role)
    } catch (caught) {
      const error = fromPortfolio(caught)
      if (done.length === 0) throw error
      const rest = sends.slice(index + 1).map((s) => s.role)
      throw new OwnerError(
        error.unconfirmed ? 'not_confirmed' : error.code,
        partialWords(tab, action, done, role, rest, error)
      )
    }
  }
}

interface Editing {
  tabId: number
  /** Fixed when a share is changed; chosen in the editor when one is added. */
  who: ShareWho | null
}

function LevelChip({ level }: { level: ShareLevel }) {
  return level === 'view' ? (
    <Chip icon={<Eye className="size-3.5" />}>View</Chip>
  ) : (
    <Chip icon={<Lock className="size-3.5" />} tone="brand">
      Restricted
    </Chip>
  )
}

function Editor({
  tab,
  editing,
  rows,
  targets,
  now,
  pending,
  onCancel,
  onSave,
}: {
  tab: TabInfo
  editing: Editing
  rows: (who: ShareWho) => TabShare[]
  targets: ReadonlyArray<{ who: ShareWho; label: string }>
  now: number
  pending: boolean
  onCancel: () => void
  onSave: (who: ShareWho, level: ShareLevel, ends: string | null) => void
}) {
  const [who, setWho] = useState<ShareWho>(editing.who ?? targets[0]?.who ?? 'all')
  const first = rows(who)[0]
  const [level, setLevel] = useState<ShareLevel>(first?.level ?? 'view')
  const [ending, setEnding] = useState(first?.expires_at != null)
  const [ends, setEnds] = useState(istInputValue(first?.expires_at ?? now + WEEK_MS))
  const id = `share-tab-${tab.id}`
  return (
    <form
      className="flex flex-col gap-3 rounded-xl border p-3"
      aria-label={`Share ${tab.name}`}
      onSubmit={(event) => {
        event.preventDefault()
        onSave(who, level, ending ? ends : null)
      }}
    >
      {editing.who === null ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-who`}>Share with</Label>
          <select
            id={`${id}-who`}
            className={SELECT}
            value={String(who)}
            onChange={(e) => setWho(e.target.value === 'all' ? 'all' : Number(e.target.value))}
          >
            {targets.map((t) => (
              <option key={String(t.who)} value={String(t.who)}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-semibold">Level</legend>
        {(['view', 'restricted'] as const).map((value) => (
          <label key={value} className={RADIO}>
            <input
              type="radio"
              name={`${id}-level`}
              className="mt-1 size-5 accent-[color:var(--brand)]"
              checked={level === value}
              onChange={() => setLevel(value)}
            />
            <span className="flex flex-col">
              <span className="font-semibold">{LEVEL_WORDS[value].label}</span>
              <span className="text-[0.8125rem] text-[color:var(--text-secondary)]">
                {LEVEL_WORDS[value].line}
              </span>
            </span>
          </label>
        ))}
      </fieldset>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-semibold">Ends</legend>
        <label className={RADIO}>
          <input
            type="radio"
            name={`${id}-end`}
            className="mt-1 size-5 accent-[color:var(--brand)]"
            checked={!ending}
            onChange={() => setEnding(false)}
          />
          <span className="font-semibold">No end</span>
        </label>
        <label className={RADIO}>
          <input
            type="radio"
            name={`${id}-end`}
            className="mt-1 size-5 accent-[color:var(--brand)]"
            checked={ending}
            onChange={() => setEnding(true)}
          />
          <span className="font-semibold">On a date and time (India time)</span>
        </label>
        {ending ? (
          <Input
            type="datetime-local"
            aria-label="Share ends at"
            className="h-12"
            value={ends}
            onChange={(e) => setEnds(e.target.value)}
          />
        ) : null}
      </fieldset>
      <div className="flex gap-2">
        <Button type="submit" className="h-12 flex-1" disabled={pending}>
          {pending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : null}
          {editing.who === null ? 'Share' : 'Save'}
        </Button>
        <Button type="button" variant="outline" className="h-12 flex-1" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

export function PortfolioTabShares({ members, now }: { members: MemberCard[]; now: number }) {
  const queryClient = useQueryClient()
  const tabs = useQuery({
    queryKey: ['sudarshan', 'family-owner', 'portfolio-tabs'],
    queryFn: async ({ signal }) => {
      const result = await portfolioCall('state', undefined, signal)
      if (!result.enabled) throw new OwnerError('off', result.message)
      return result.data.tabs.filter((t) => !t.archived)
    },
    retry: false,
  })
  const shares = useQuery({
    queryKey: PORTFOLIO_SHARES_KEY,
    queryFn: ({ signal }) => portfolioShares.list(signal),
    refetchOnWindowFocus: true,
    retry: false,
  })
  const [editing, setEditing] = useState<Editing | null>(null)
  const reread = () => queryClient.invalidateQueries({ queryKey: PORTFOLIO_SHARES_KEY })
  const once = useOnce((_said, error) => {
    if (error === null || error.unconfirmed || error.code === 'conflict') void reread()
  })
  const rows = shares.data?.shares ?? []
  const nameOf = (who: ShareWho) =>
    who === 'all' ? 'every member' : (members.find((m) => m.id === who)?.display_name ?? 'them')
  const untilWords = (ends: string | null) => {
    const at = ends === null ? null : fromIstInput(ends)
    return at === null ? '' : `, until ${whenOf(at, now)}`
  }

  const save = (tab: TabInfo, who: ShareWho, level: ShareLevel, ends: string | null) => {
    void once
      .run(
        async () => {
          let expires: number | null = null
          if (ends !== null) {
            expires = fromIstInput(ends)
            if (expires === null) throw new OwnerError('invalid', PICK_END)
            if (expires <= Date.now()) throw new OwnerError('invalid', END_AFTER_NOW)
          }
          const sendFor = (viewer: string) => () =>
            portfolioShares.set({
              tab_id: tab.id,
              viewer,
              level,
              expires_at: expires,
              version:
                rows.find((r) => r.tab_id === tab.id && r.viewer === viewer)?.version ?? null,
            })
          try {
            if (who === 'all') {
              await byRole(
                tab.name,
                'share',
                ROLES.map((role) => ({ role, send: sendFor(roleViewer(role)) }))
              )
            } else {
              await sendFor(String(who))()
            }
          } finally {
            void reread()
          }
          return true
        },
        () =>
          `${tab.name} is shared with ${nameOf(who)}: ${LEVEL_WORDS[level].label}${untilWords(ends)}.`
      )
      .then((done) => {
        if (done) setEditing(null)
      })
  }

  const stop = (tab: TabInfo, line: ShareLine) => {
    void once.run(
      async () => {
        try {
          if (line.who === 'all') {
            await byRole(
              tab.name,
              'stop',
              line.rows.map((row) => ({
                role: row.viewer.slice(5) as Role,
                send: () =>
                  portfolioShares.remove({
                    tab_id: tab.id,
                    viewer: row.viewer,
                    version: row.version,
                  }),
              }))
            )
          } else {
            const [row] = line.rows
            await portfolioShares.remove({
              tab_id: tab.id,
              viewer: row.viewer,
              version: row.version,
            })
          }
        } finally {
          void reread()
        }
        return true
      },
      () =>
        `${tab.name} is no longer shared with ${line.who === null ? line.label.toLowerCase() : nameOf(line.who)}.`
    )
  }

  const shared = rows.some((r) => r.viewer.startsWith('role:'))
  const unreached = shares.data
    ? members.filter((m) => {
        const floor = floorOf(shares.data, m.id)
        return floor === null || floor.role !== m.role
      })
    : []

  return (
    <section aria-labelledby="share-tabs" className={`${CARD} flex flex-col gap-3`}>
      <h2 id="share-tabs" tabIndex={-1} className="scroll-mt-20 text-[1.0625rem] font-bold outline-none">
        Portfolio tabs
      </h2>
      <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">{TABS_NOTE}</p>
      <SaidLine said={once.said} />
      {shared && unreached.length > 0 ? (
        <p className="flex items-start gap-2 text-[0.875rem] text-[color:var(--warning-text)]">
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {`Tabs shared with every member do not reach ${unreached.map((m) => m.display_name).join(' and ')} yet: their privacy has not reached the portfolio. Open their card under Members and save.`}
        </p>
      ) : null}
      {tabs.isPending || shares.isPending ? (
        <p className="flex items-center gap-2 text-[0.9375rem]">
          <Loader2 aria-hidden="true" className="size-4 animate-spin" />
          Reading your portfolio tabs.
        </p>
      ) : tabs.isError || shares.isError ? (
        <p role="alert" className="text-[0.9375rem] text-[color:var(--warning-text)]">
          {fromPortfolio(tabs.error ?? shares.error).message}
        </p>
      ) : tabs.data.length === 0 ? (
        <p className="text-[0.9375rem]">You have no portfolio tab to share yet.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
          {tabs.data.map((tab) => {
            const lines = linesFor(rows, tab.id, members, now)
            const own = new Set(lines.filter((l) => typeof l.who === 'number').map((l) => l.who))
            const targets = [
              { who: 'all' as const, label: 'Every member' },
              ...members
                .filter((m) => !own.has(m.id))
                .map((m) => ({ who: m.id, label: m.display_name })),
            ]
            const byAll = lines.some((l) => l.who === 'all' || l.who === null)
            return (
              <li key={tab.id} className="flex flex-col gap-2 py-3">
                <span className="flex items-center gap-2 font-semibold">
                  <FolderOpen aria-hidden="true" className="size-4 shrink-0" />
                  {tab.name}
                </span>
                {tab.system ? (
                  <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
                    {UPSTOX_NOTE}
                  </p>
                ) : null}
                {lines.length === 0 ? (
                  <span className="text-[0.875rem] text-[color:var(--text-secondary)]">
                    Not shared
                  </span>
                ) : (
                  <ul className="flex flex-col gap-1" aria-label={`${tab.name} is shared with`}>
                    {lines.map((line) => (
                      <li key={line.key} className={`${ROW} flex-wrap`}>
                        <div className="flex min-w-0 flex-col gap-1">
                          <span className="truncate text-[0.9375rem]">{line.label}</span>
                          <span className="flex flex-wrap items-center gap-1.5 text-[0.8125rem] text-[color:var(--text-tertiary)]">
                            <LevelChip level={line.level} />
                            {line.ended ? (
                              <Chip icon={<Clock className="size-3.5" />} tone="warn">
                                {`Ended ${whenOf(line.expiresAt as number, now)}`}
                              </Chip>
                            ) : line.expiresAt !== null ? (
                              <span>{`Until ${whenOf(line.expiresAt, now)}`}</span>
                            ) : null}
                          </span>
                          {line.ended && typeof line.who === 'number' && byAll ? (
                            <span className="text-[0.8125rem] text-[color:var(--warning-text)]">
                              {ENDED_BLOCKS}
                            </span>
                          ) : null}
                        </div>
                        <div className="flex gap-2">
                          {line.who !== null ? (
                            <Button
                              type="button"
                              variant="outline"
                              className="h-12"
                              disabled={once.pending}
                              aria-label={`Change how ${tab.name} is shared with ${line.label}`}
                              onClick={() => setEditing({ tabId: tab.id, who: line.who })}
                            >
                              Change
                            </Button>
                          ) : null}
                          <Button
                            type="button"
                            variant="outline"
                            className="h-12"
                            disabled={once.pending}
                            aria-label={`Stop sharing ${tab.name} with ${line.label}`}
                            onClick={() => stop(tab, line)}
                          >
                            Stop
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                {editing?.tabId === tab.id ? (
                  <Editor
                    key={`${tab.id}:${String(editing.who)}`}
                    tab={tab}
                    editing={editing}
                    rows={(who) => rowsFor(rows, tab.id, who)}
                    targets={targets}
                    now={now}
                    pending={once.pending}
                    onCancel={() => setEditing(null)}
                    onSave={(who, level, ends) => save(tab, who, level, ends)}
                  />
                ) : members.length > 0 ? (
                  <Button
                    type="button"
                    variant="outline"
                    className="h-12 self-start"
                    disabled={once.pending}
                    aria-label={`Share ${tab.name}`}
                    onClick={() => setEditing({ tabId: tab.id, who: null })}
                  >
                    Share
                  </Button>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
