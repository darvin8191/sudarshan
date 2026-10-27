import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, Loader2, Play, TriangleAlert } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { type KillSwitchState, type PreviewCounts, riskApi, type SwitchAnswer } from '@/api/risk'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { showToast } from '@/utils/toast'
import { noteKillSwitch } from './killSwitch'
import { clockOf, RISK_AUDIT_KEY, RISK_STATUS_KEY, surfaceOf } from './parts'
import { PHRASE_HINT } from './TurnOnSheet'

const count = new Intl.NumberFormat('en-IN')

function many(value: number, one: string, more: string): string {
  return `${count.format(value)} ${value === 1 ? one : more}`
}

/** What is open now, before trading starts again (design 3.8: a fresh summary). */
export function openNow(counts: PreviewCounts | null | undefined): string {
  if (!counts) return 'Open positions and orders could not be read just now.'
  const parts = [
    many(counts.positions, 'open position', 'open positions'),
    many(counts.orders, 'open order', 'open orders'),
  ]
  if (counts.gtts !== null && counts.gtts !== undefined) {
    parts.push(many(counts.gtts, 'GTT', 'GTTs'))
  }
  return `Open now: ${parts.join(', ')}.`
}

/** Seconds until it may be turned off; 0 when it may. */
export function secondsLeft(canResumeAt: string | null | undefined, now: number): number {
  if (!canResumeAt) return 0
  const at = new Date(canResumeAt).getTime()
  if (Number.isNaN(at)) return 0
  return Math.max(0, Math.ceil((at - now) / 1000))
}

/**
 * Resuming (M20.7, amendment R3): the phrase, and no sooner than a minute after it went on,
 * with a countdown and what is open now. Only the owner's session can resume: never a script,
 * never an API key. Positions, orders, the order mode and strategies are not changed by it.
 */
export function ResumeSheet({
  open,
  onOpenChange,
  killSwitch,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  killSwitch: KillSwitchState | undefined
}) {
  const queryClient = useQueryClient()
  const [phrase, setPhrase] = useState('')
  const [sending, setSending] = useState(false)
  const [refused, setRefused] = useState<SwitchAnswer | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const busy = useRef(false)
  const phraseId = useId()

  useEffect(() => {
    if (!open) return
    setPhrase('')
    setRefused(null)
  }, [open])

  const left = secondsLeft(killSwitch?.can_resume_at, now)
  // The countdown's own clock: a second at a time, only while the sheet is open and counting.
  useEffect(() => {
    if (!open || left <= 0) return
    const timer = setInterval(() => setNow(Date.now()), 1_000)
    return () => clearInterval(timer)
  }, [open, left])

  const preview = useQuery({
    queryKey: ['sudarshan-risk-preview'],
    queryFn: riskApi.preview,
    enabled: open,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })

  const send = async () => {
    if (busy.current || !phrase.trim() || left > 0) return
    busy.current = true
    setSending(true)
    try {
      let answer: SwitchAnswer | null = null
      try {
        answer = await riskApi.resume(phrase)
      } catch {
        answer = null
      }
      void queryClient.invalidateQueries({ queryKey: RISK_STATUS_KEY })
      void queryClient.invalidateQueries({ queryKey: RISK_AUDIT_KEY })
      if (answer?.state) noteKillSwitch(answer.state)
      if (answer?.ok) {
        showToast.success(answer.message)
        onOpenChange(false)
        return
      }
      if (answer?.code === 400) setPhrase('')
      setRefused(
        answer ?? {
          code: 0,
          ok: false,
          message: 'No answer came back. Check this page in a few seconds before trying again.',
          state: null,
          activated: false,
        }
      )
    } finally {
      busy.current = false
      setSending(false)
    }
  }

  const since = clockOf(killSwitch?.since)
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => {
        if (!sending) onOpenChange(next)
      }}
      title="Resume trading"
      description={
        since ? `Kill switch on since ${since}, from ${surfaceOf(killSwitch?.surface)}` : undefined
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          void send()
        }}
      >
        <p className="text-[0.9375rem] leading-snug">
          New orders go again from every place. Positions, orders and strategies are not changed.
        </p>
        <p className="text-[0.9375rem] leading-snug" data-testid="resume-open-now">
          {preview.isPending ? 'Reading what is open now.' : openNow(preview.data?.counts)}
        </p>
        <p
          className="flex items-center gap-2 text-[0.9375rem] font-semibold"
          data-testid="resume-countdown"
        >
          <Clock aria-hidden="true" className="size-5 shrink-0" />
          {left > 0
            ? `It stays on for at least a minute: you can resume in ${left} ${left === 1 ? 'second' : 'seconds'}.`
            : 'You can resume now.'}
        </p>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor={phraseId} className="text-[0.9375rem] font-semibold">
            Confirm phrase
          </Label>
          <Input
            id={phraseId}
            value={phrase}
            onChange={(event) => setPhrase(event.target.value)}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="characters"
            spellCheck={false}
            className="h-12 text-base"
            aria-describedby={`${phraseId}-hint`}
          />
          <p id={`${phraseId}-hint`} className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
            {PHRASE_HINT}
          </p>
        </div>

        {refused && (
          <p role="alert" className="sd-risk-note">
            <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
            {refused.message}
          </p>
        )}

        <Button
          type="submit"
          className="h-12 text-base"
          disabled={sending || !phrase.trim() || left > 0}
        >
          {sending ? (
            <>
              <Loader2 aria-hidden="true" className="size-5 animate-spin" />
              Resuming
            </>
          ) : (
            <>
              <Play aria-hidden="true" className="size-5" />
              Resume trading
            </>
          )}
        </Button>
      </form>
    </BottomSheet>
  )
}
