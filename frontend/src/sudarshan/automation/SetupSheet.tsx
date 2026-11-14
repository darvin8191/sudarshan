// SUDARSHAN M12.1: Alert set-up. The link for TradingView's Webhook URL (shown only in the answer
// that made it, kept in this sheet's memory and nowhere else), the alert message for each kind of
// Pine v6 alert, and Check: one alert's text read as the server would read it, with no order.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link2, Loader2, SearchCheck, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Segmented } from '@/sudarshan/journal/Segmented'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { automationApi, LIST_KEY, oneKey } from './api'
import { Copy } from './Copy'
import { ToneIcon } from './StrategyCard'
import type { AlertMessages, CheckAnswer, CreateAnswer, Strategy } from './types'
import { toneOf } from './words'

type Form = 'strategy' | 'indicator' | 'condition'
const FORMS: ReadonlyArray<{ id: Form; label: string }> = [
  { id: 'strategy', label: 'Strategy' },
  { id: 'indicator', label: 'alert()' },
  { id: 'condition', label: 'Condition' },
]
const STEPS: Record<Form, string[]> = {
  strategy: [
    'On the strategy’s chart, add an alert with the strategy as its condition and choose "Order fills only".',
    'Paste this message, and put the link in Webhook URL.',
    'Keep calc_on_every_tick = false in the script.',
    'After you change the script or its inputs, delete the alert and make it again.',
    'One alert per strategy: a second one doubles every order.',
  ],
  indicator: [
    'Put f_msg in your indicator and call alert() where it goes long, short or flat.',
    'Add an alert on the indicator with "Any alert() function call", and put the link in Webhook URL.',
  ],
  condition: [
    'Make one alert for each message below, "Once Per Bar Close", with the link in Webhook URL.',
    'Each message says where the strategy should be after it: long, short or flat.',
  ],
}
const BOX =
  'max-h-56 overflow-auto rounded-xl border bg-[color:var(--bg-canvas)] p-3 font-mono text-[0.75rem] leading-relaxed break-all whitespace-pre-wrap select-all'

function Message({ text, what }: { text: string; what: string }) {
  return (
    <div className="flex flex-col gap-2">
      <pre className={BOX}>{text}</pre>
      <Copy text={text} what={what} />
    </div>
  )
}

/** The side a fixed message points at, for its copy button. */
const sideOf = (text: string): string => /"position": "(\w+)"/.exec(text)?.[1] ?? 'message'

function Messages({ messages }: { messages: AlertMessages }) {
  const [form, setForm] = useState<Form>('strategy')
  return (
    <section className="flex flex-col gap-3" aria-label="Alert message">
      <h3 className="text-[1rem] font-bold">2. The alert message</h3>
      <Segmented label="Kind of alert" value={form} options={FORMS} onChange={setForm} />
      <ol className="list-decimal pl-5 text-[0.875rem] leading-snug text-[color:var(--text-secondary)]">
        {STEPS[form].map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      {form === 'condition' ? (
        messages.condition.map((text) => (
          <Message key={text} text={text} what={`the ${sideOf(text)} message`} />
        ))
      ) : (
        <Message
          text={messages[form]}
          what={form === 'strategy' ? 'the message' : 'the script lines'}
        />
      )}
    </section>
  )
}

function Check({ id }: { id: number }) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [answer, setAnswer] = useState<CheckAnswer | string | null>(null)
  const run = async () => {
    setSending(true)
    const got = await automationApi.check(id, text)
    setAnswer(got.ok ? got : got.message)
    setSending(false)
  }
  return (
    <section className="flex flex-col gap-3" aria-label="Check">
      <h3 className="text-[1rem] font-bold">3. Check a message</h3>
      <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
        Paste one alert as TradingView would send it, with real values in place of the {'{{ }}'}{' '}
        parts. SUDARSHAN says what it would do. Nothing is sent.
      </p>
      <Textarea
        aria-label="Alert text to check"
        className="min-h-28 font-mono text-[0.8125rem]"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <Button
        type="button"
        variant="outline"
        className="h-12 self-start"
        disabled={sending || !text.trim()}
        onClick={run}
      >
        {sending ? (
          <Loader2 aria-hidden="true" className="size-5 animate-spin" />
        ) : (
          <SearchCheck aria-hidden="true" className="size-5" />
        )}
        Check
      </Button>
      {typeof answer === 'string' && (
        <p role="alert" className="sd-risk-note">
          <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
          {answer}
        </p>
      )}
      {answer && typeof answer !== 'string' && (
        <output
          data-testid="auto-check"
          data-verdict={answer.verdict}
          className="flex flex-col gap-2 text-[0.875rem]"
        >
          <span className="flex items-start gap-1.5 font-semibold">
            <ToneIcon tone={toneOf(answer.verdict)} />
            {answer.sentence}
          </span>
          {answer.order && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-xl border p-3">
              {(['exchange', 'symbol', 'action', 'product', 'quantity', 'position_size'] as const)
                .filter((key) => answer.order?.[key] !== undefined)
                .map((key) => (
                  <div key={key} className="contents">
                    <dt className="text-[color:var(--text-secondary)]">
                      {key === 'position_size'
                        ? 'Position after'
                        : key[0].toUpperCase() + key.slice(1)}
                    </dt>
                    <dd className="font-medium break-all">{String(answer.order?.[key])}</dd>
                  </div>
                ))}
            </dl>
          )}
        </output>
      )}
    </section>
  )
}

export function SetupSheet({
  open,
  onClose,
  strategy,
  fresh,
  onFresh,
}: {
  open: boolean
  onClose: () => void
  strategy: Strategy | null
  /** The answer that made this strategy's link, while the page still holds it. */
  fresh: CreateAnswer | null
  onFresh: (answer: CreateAnswer) => void
}) {
  const queryClient = useQueryClient()
  const id = strategy?.id ?? 0
  const one = useQuery({
    queryKey: oneKey(id),
    queryFn: () => automationApi.one(id),
    enabled: open && id > 0 && !fresh,
  })
  const [asking, setAsking] = useState(false)
  const [sending, setSending] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const busy = useRef(false)
  useEffect(() => {
    if (open) {
      setAsking(false)
      setRefusal(null)
    }
  }, [open])
  const messages = fresh?.messages ?? one.data?.messages
  const link = fresh ? `${window.location.origin}${fresh.link_path}` : null

  const newLink = async () => {
    if (busy.current || !strategy) return
    busy.current = true
    setSending(true)
    const answer = await automationApi.newLink(strategy.id, strategy.version)
    busy.current = false
    setSending(false)
    void queryClient.invalidateQueries({ queryKey: LIST_KEY })
    if (!answer.ok) return setRefusal(answer.message)
    setAsking(false)
    onFresh(answer)
  }

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && !sending && onClose()}
      title={`Alert set-up: ${strategy?.name ?? ''}`}
      description={strategy?.trades}
      step={asking ? 'ask' : 'show'}
    >
      <div className="flex flex-col gap-5">
        <section className="flex flex-col gap-3" aria-label="Alert link">
          <h3 className="text-[1rem] font-bold">1. The link for Webhook URL</h3>
          {link ? (
            <>
              <pre data-testid="auto-link" className={BOX}>
                {link}
              </pre>
              <Copy text={link} what="the link" />
              <p className="text-[0.875rem] font-semibold text-[color:var(--warning-text)]">
                Copy it now: it is shown only this once. Anyone who has this link can send this
                strategy’s alerts, so keep it to TradingView.
              </p>
            </>
          ) : asking ? (
            <>
              <p className="text-[0.9375rem]">
                The old link stops working at once. Every TradingView alert of {strategy?.name} then
                needs the new link in its Webhook URL, or its alerts are ignored.
              </p>
              <div className="grid grid-cols-2 gap-3">
                <Button variant="outline" className="h-12" onClick={() => setAsking(false)}>
                  Keep the old link
                </Button>
                <Button variant="destructive" className="h-12" disabled={sending} onClick={newLink}>
                  {sending && <Loader2 aria-hidden="true" className="size-5 animate-spin" />}
                  Make a new link
                </Button>
              </div>
            </>
          ) : (
            <>
              <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
                This strategy’s link ends in {strategy?.token_tail}. SUDARSHAN shows a link only
                when it is made and does not keep it. If you no longer have it, make a new one.
              </p>
              <Button
                variant="outline"
                className="h-12 self-start"
                aria-haspopup="dialog"
                onClick={() => setAsking(true)}
              >
                <Link2 aria-hidden="true" className="size-5" />
                New link
              </Button>
            </>
          )}
          {refusal && (
            <p role="alert" className="sd-risk-note">
              <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
              {refusal}
            </p>
          )}
        </section>
        {messages ? (
          <Messages messages={messages} />
        ) : one.isError ? (
          <p role="alert" className="sd-risk-note">
            <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
            {one.error.message}
          </p>
        ) : (
          <Loader2 aria-label="Reading the messages" className="size-6 animate-spin" />
        )}
        {strategy && <Check id={strategy.id} />}
        <p className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
          TradingView sends alerts to a link only from an account with two-factor sign-in on.
        </p>
      </div>
    </BottomSheet>
  )
}
