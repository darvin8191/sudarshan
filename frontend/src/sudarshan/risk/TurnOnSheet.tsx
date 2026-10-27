import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, CircleCheck, Loader2, OctagonX, TriangleAlert, Undo2 } from 'lucide-react'
import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { type RiskStage, riskApi, type SwitchAnswer } from '@/api/risk'
import { tradingApi } from '@/api/trading'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { actsOn, type Book, turnOnHeld } from './holds'
import { noteKillSwitch } from './killSwitch'
import { RISK_AUDIT_KEY, RISK_STATUS_KEY, StepList } from './parts'

/** The phrase's hint: R3 ships STOP, which the owner may change in Settings > Risk. */
export const PHRASE_HINT = 'Type your confirm phrase. It is STOP unless you changed it.'
export const NO_ANSWER =
  'No answer came back. The kill switch may still be turning on: this page shows it in a few seconds. Check Orders and Positions.'

/** Where it acts, in the sheet's own words. */
export function reachOf(stage: RiskStage | undefined): string {
  if (stage === 'on') return 'Live at Upstox and the sandbox'
  if (stage === 'sandbox') return 'The sandbox only: your Upstox account is not touched'
  return 'Not available on this server'
}

/** The book the holds are taken from: the page's mode, as /api/v1 reads it. */
async function readBook(apiKey: string): Promise<Book> {
  const [positions, orders] = await Promise.all([
    tradingApi.getPositions(apiKey),
    tradingApi.getOrders(apiKey),
  ])
  return {
    positions: Array.isArray(positions?.data) ? positions.data : [],
    orders: Array.isArray(orders?.data?.orders) ? orders.data.orders : [],
  }
}

function Line({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <li className="flex items-start gap-2.5 py-1.5 text-[0.9375rem] leading-snug">
      <span className="mt-0.5 shrink-0 text-[color:var(--text-secondary)]">{icon}</span>
      <span>{children}</span>
    </li>
  )
}

/**
 * Turning the kill switch on (M20.7, amendments R1 to R3): what will happen, counted fresh;
 * Exit all as a separate tick, unticked; the confirm phrase. Like every sheet it ignores taps
 * for half a second after it opens (BottomSheet), and it sends once however it is tapped. The
 * rows it acts on are held from the tap (holds.ts).
 */
export function TurnOnSheet({
  open,
  onOpenChange,
  stage,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  stage: RiskStage | undefined
}) {
  const queryClient = useQueryClient()
  const sandbox = useThemeStore((s) => s.appMode) === 'analyzer'
  const apiKey = useAuthStore((s) => s.apiKey)
  const [exitAll, setExitAll] = useState(false)
  const [phrase, setPhrase] = useState('')
  const [sending, setSending] = useState(false)
  const [answer, setAnswer] = useState<SwitchAnswer | null | undefined>(undefined)
  const busy = useRef(false)
  const phraseId = useId()
  const exitAllId = useId()

  useEffect(() => {
    if (!open) return
    setExitAll(false)
    setPhrase('')
    setAnswer(undefined)
  }, [open])

  const preview = useQuery({
    queryKey: ['sudarshan-risk-preview'],
    queryFn: riskApi.preview,
    enabled: open,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })
  const holdable = actsOn(stage, sandbox) && Boolean(apiKey)
  const book = useQuery({
    queryKey: ['sudarshan-risk-book', sandbox],
    queryFn: () => readBook(apiKey ?? ''),
    enabled: open && holdable,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })

  const send = async () => {
    if (busy.current || !phrase.trim()) return
    busy.current = true
    setSending(true)
    try {
      const result = await turnOnHeld({
        book: book.data ?? null,
        sandbox,
        stage,
        exitAll,
        send: () => riskApi.turnOn(exitAll, phrase),
      })
      if (result?.state) noteKillSwitch(result.state)
      if (result && !result.ok && result.code === 400) setPhrase('')
      setAnswer(result)
      void queryClient.invalidateQueries({ queryKey: RISK_STATUS_KEY })
      void queryClient.invalidateQueries({ queryKey: RISK_AUDIT_KEY })
    } finally {
      busy.current = false
      setSending(false)
    }
  }

  const done = answer === null || answer?.ok === true

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => {
        if (!sending) onOpenChange(next)
      }}
      title="Turn kill switch on"
      description={reachOf(stage)}
    >
      {done ? (
        <div className="flex flex-col gap-4">
          {answer === null ? (
            <p role="alert" className="sd-risk-note">
              <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
              {NO_ANSWER}
            </p>
          ) : (
            <>
              <output className="flex items-start gap-2 text-[0.9375rem] font-semibold">
                <OctagonX
                  aria-hidden="true"
                  className="mt-0.5 size-5 shrink-0 text-[color:var(--down)]"
                />
                {answer?.message}
              </output>
              {answer?.activated && answer.state?.report && (
                <StepList steps={answer.state.report.steps} />
              )}
            </>
          )}
          <Button className="h-12" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </div>
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            void send()
          }}
        >
          <ul className="divide-y divide-[color:var(--divider)]">
            <Line icon={<Ban aria-hidden="true" className="size-5" />}>
              New orders are refused from every place: this app, scripts and alerts, strategies and
              Telegram.
            </Line>
            <Line icon={<Undo2 aria-hidden="true" className="size-5" />}>
              Exits and cancels still go. Python strategies keep running; the agent stops.
            </Line>
            <Line icon={<TriangleAlert aria-hidden="true" className="size-5" />}>
              {preview.isPending ? (
                <span className="inline-flex items-center gap-2 text-[color:var(--text-secondary)]">
                  <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                  Counting open orders and GTTs
                </span>
              ) : (
                <span data-testid="kill-switch-preview">
                  {preview.data?.message ??
                    'The open orders and GTTs could not be counted just now. Every open order and GTT will be cancelled, stop-loss orders too.'}
                </span>
              )}
            </Line>
          </ul>

          <div className="flex min-h-12 items-start gap-3 rounded-xl border border-[color:var(--border-strong)] px-3 py-3">
            <Checkbox
              id={exitAllId}
              checked={exitAll}
              onCheckedChange={(value) => setExitAll(value === true)}
              className="mt-0.5 size-5"
            />
            <Label
              htmlFor={exitAllId}
              className="flex flex-col items-start gap-0.5 text-[0.9375rem]"
            >
              <span className="font-semibold">Also exit all positions</span>
              <span className="font-normal text-[color:var(--text-secondary)]">
                Every open position is closed at market, as Exit all does.
              </span>
            </Label>
          </div>

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
            <p
              id={`${phraseId}-hint`}
              className="text-[0.8125rem] text-[color:var(--text-tertiary)]"
            >
              {PHRASE_HINT}
            </p>
          </div>

          {answer && !answer.ok && (
            <p role="alert" className="sd-risk-note">
              <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
              {answer.message}
            </p>
          )}

          <Button
            type="submit"
            variant="destructive"
            className="h-12 text-base"
            disabled={sending || !phrase.trim() || stage === 'off'}
          >
            {sending ? (
              <>
                <Loader2 aria-hidden="true" className="size-5 animate-spin" />
                Turning on
              </>
            ) : (
              <>
                <OctagonX aria-hidden="true" className="size-5" />
                Turn kill switch on
              </>
            )}
          </Button>
          {sending && (
            <p className="flex items-center gap-2 text-[0.8125rem] text-[color:var(--text-secondary)]">
              <CircleCheck aria-hidden="true" className="size-4" />
              Cancelling and exiting at Upstox can take a few seconds.
            </p>
          )}
        </form>
      )}
    </BottomSheet>
  )
}
