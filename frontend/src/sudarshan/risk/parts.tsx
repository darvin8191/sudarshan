import { CircleCheck, TriangleAlert } from 'lucide-react'
import type { KillSwitchStep } from '@/api/risk'

export const RISK_STATUS_KEY = ['sudarshan-risk-status'] as const
export const RISK_AUDIT_KEY = ['sudarshan-risk-audit'] as const

const clock = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const dayClock = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

/** "10:42" in India, or '' when there is no time. */
export function clockOf(iso: string | null | undefined): string {
  if (!iso) return ''
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? '' : clock.format(at)
}

/** "27 Oct, 10:42:07 IST", or '' when there is no time. */
export function stampOf(iso: string | null | undefined): string {
  if (!iso) return ''
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? '' : `${dayClock.format(at)} IST`
}

const SURFACES: Record<string, string> = {
  app: 'the app',
  telegram: 'Telegram',
  api: 'a script or an alert',
}

/** Where the switch was turned on from, as the trader would say it. */
export function surfaceOf(surface: string | null | undefined): string {
  return (surface && SURFACES[surface]) || 'SUDARSHAN'
}

/** What each step of an activation did: an icon and its sentence, never colour alone. */
export function StepList({ steps }: { steps: KillSwitchStep[] }) {
  return (
    <ul
      className="divide-y divide-[color:var(--divider)] rounded-xl border"
      aria-label="What it did"
    >
      {steps.map((step, index) => (
        <li
          key={`${step.step}-${index}`}
          className="flex items-start gap-2.5 px-3 py-2.5 text-[0.875rem] leading-snug"
        >
          {step.ok ? (
            <CircleCheck
              aria-label="Done"
              className="mt-0.5 size-4 shrink-0 text-[color:var(--live-ink)]"
            />
          ) : (
            <TriangleAlert
              aria-label="Needs a look"
              className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
            />
          )}
          <span>{step.words}</span>
        </li>
      ))}
    </ul>
  )
}
