// SUDARSHAN P6 D7b (M20.6): the family service's own state: on or off, the store's version, what
// the install check found, and the Family bot card (Polling, Token missing, Telegram refused the
// token). Read from the bridge's `state`; the page reads it again every 30 seconds while open.
import { Bot, CircleCheck, OctagonX, ShieldAlert, ShieldCheck, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import type { AdminState } from '../types'
import { CARD, Chip } from './parts'
import { botOf, checkOf } from './stateExtra'
import { clockOf } from './words'

export const CHECK_OK = "Checked at install: the family service cannot read the account's secrets"
export const CHECK_BAD =
  "The install check found that the family service can read the account's secrets. Run the family set-up again and share nothing until it passes."
export const CHECK_UNKNOWN = 'The result of the install check is not reported yet.'
export const BOT_MISSING = 'Token missing: see sudarshan/family/README.md'
export const BOT_REFUSED = 'Telegram refused the token'
export const BOT_UNKNOWN = "The bot's state is not reported yet."

function Line({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <p className="flex items-start gap-2.5 text-[0.9375rem] leading-snug">
      <span aria-hidden="true" className="mt-0.5 shrink-0 text-[color:var(--text-secondary)]">
        {icon}
      </span>
      <span>{children}</span>
    </p>
  )
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

export function BotCard({ state }: { state: AdminState }) {
  const bot = botOf(state)
  let line: ReactNode = BOT_UNKNOWN
  let icon: ReactNode = <Bot className="size-4" />
  if (bot?.state === 'polling') {
    line = `Polling · ${bot.last_message_at === null ? 'no message yet' : `last message ${clockOf(bot.last_message_at)}`}`
    icon = <CircleCheck className="size-4 text-[color:var(--up)]" />
  } else if (bot?.state === 'token_missing') {
    line = BOT_MISSING
    icon = <TriangleAlert className="size-4 text-[color:var(--warning-text)]" />
  } else if (bot?.state === 'token_refused') {
    line = BOT_REFUSED
    icon = <OctagonX className="size-4 text-[color:var(--down)]" />
  }
  return (
    <section aria-labelledby="family-bot" className={`${CARD} flex flex-col gap-2`}>
      <h2 id="family-bot" className="flex items-center gap-2 text-[1.0625rem] font-bold">
        <Bot aria-hidden="true" className="size-5" />
        Family bot
      </h2>
      <Line icon={icon}>{line}</Line>
    </section>
  )
}

export function ServiceCard({ state }: { state: AdminState }) {
  const check = checkOf(state)
  return (
    <div className="flex flex-col gap-4">
      <section aria-labelledby="family-service" className={`${CARD} flex flex-col gap-3`}>
        <div className="flex items-center gap-2">
          <h2 id="family-service" className="flex-1 text-[1.0625rem] font-bold">
            Family service
          </h2>
          {state.enabled ? (
            <Chip tone="good" icon={<CircleCheck className="size-3.5" />}>
              On
            </Chip>
          ) : (
            <Chip tone="warn" icon={<OctagonX className="size-3.5" />}>
              Off
            </Chip>
          )}
        </div>
        {!state.enabled && state.message ? (
          <p className="text-[0.9375rem]">{state.message}</p>
        ) : null}
        <p className="text-[0.9375rem] text-[color:var(--text-secondary)]">
          {state.store_version === null
            ? 'Store version not read yet'
            : `Store version ${state.store_version}`}
        </p>
        <p className="text-[0.9375rem] text-[color:var(--text-secondary)]">
          {`${plural(state.members, 'member', 'members')} · ${plural(state.sessions, 'signed-in device', 'signed-in devices')} · ${plural(state.sockets, 'open screen', 'open screens')}`}
        </p>
        {check === null ? (
          <Line icon={<ShieldAlert className="size-4" />}>{CHECK_UNKNOWN}</Line>
        ) : check.ok ? (
          <Line icon={<ShieldCheck className="size-4 text-[color:var(--up)]" />}>{CHECK_OK}</Line>
        ) : (
          <Line icon={<ShieldAlert className="size-4 text-[color:var(--down)]" />}>
            {CHECK_BAD}
          </Line>
        )}
      </section>
      <BotCard state={state} />
    </div>
  )
}
