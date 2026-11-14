// SUDARSHAN M12.1: the switch of one strategy. Stop and Start, and where its orders go. The
// default Stop keeps the exit alert working (owner decision D6); Stop everything asks again,
// because it leaves a position without its exit. Each button sends once however it is tapped.
import { useQueryClient } from '@tanstack/react-query'
import { FlaskConical, Loader2, LogOut, Pause, Play, Radio, TriangleAlert } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { PhraseInput } from '@/sudarshan/risk/PhraseInput'
import { PHRASE_HINT } from '@/sudarshan/risk/phrase'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { type Answer, automationApi, LIST_KEY } from './api'
import { ModeChip, StateChip } from './StrategyCard'
import type { ListAnswer, OneAnswer, Strategy } from './types'
import { contractName } from './words'

export function SwitchSheet({
  open,
  onClose,
  strategy,
  list,
}: {
  open: boolean
  onClose: () => void
  strategy: Strategy | null
  list: ListAnswer
}) {
  const queryClient = useQueryClient()
  const [asking, setAsking] = useState(false)
  const [phrase, setPhrase] = useState('')
  const [sending, setSending] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const busy = useRef(false)
  const phraseId = useId()
  useEffect(() => {
    if (!open) return
    setAsking(false)
    setPhrase('')
    setRefusal(null)
  }, [open])
  if (!strategy) return null
  const { id, version, name, state } = strategy
  const live = strategy.mode === 'live'
  const held = strategy.held_symbol ? contractName(strategy.held_symbol) : null

  const send = async (what: string, call: () => Promise<Answer<OneAnswer>>) => {
    if (busy.current) return
    busy.current = true
    setSending(what)
    setRefusal(null)
    const answer = await call()
    busy.current = false
    setSending(null)
    // Read again either way: a refusal may mean another screen changed it first.
    void queryClient.invalidateQueries({ queryKey: LIST_KEY })
    if (!answer.ok) {
      setPhrase('')
      return setRefusal(answer.message)
    }
    queryClient.setQueryData<ListAnswer>(LIST_KEY, (old) =>
      old
        ? { ...old, strategies: old.strategies.map((s) => (s.id === id ? answer.strategy : s)) }
        : old
    )
    onClose()
  }
  const spin = (what: string) =>
    sending === what && <Loader2 aria-hidden="true" className="size-5 animate-spin" />
  const phraseBox = (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={phraseId} className="text-[0.9375rem] font-semibold">
        Confirm phrase
      </Label>
      <PhraseInput
        id={phraseId}
        value={phrase}
        onChange={setPhrase}
        describedBy={`${phraseId}-hint`}
      />
      <p id={`${phraseId}-hint`} className="text-[0.8125rem] text-[color:var(--text-tertiary)]">
        {PHRASE_HINT}
      </p>
    </div>
  )

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && !sending && onClose()}
      title={state === 'stopped' ? `Start ${name}` : `Stop ${name}`}
      description={strategy.trades}
      step={asking ? 'ask' : 'choose'}
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-1.5">
          <StateChip state={state} />
          <ModeChip mode={strategy.mode} />
        </div>
        {asking ? (
          <>
            <p className="text-[0.9375rem] leading-snug">
              Every alert of {name} will be ignored, its exit alert too.{' '}
              {held
                ? `${name} holds ${held}: nothing will close it. Close it yourself in Positions.`
                : 'If it has a position, nothing will close it.'}
            </p>
            <div className="grid grid-cols-2 gap-3">
              <Button variant="outline" className="h-12" onClick={() => setAsking(false)}>
                Go back
              </Button>
              <Button
                variant="destructive"
                className="h-12"
                disabled={sending !== null}
                onClick={() => send('all', () => automationApi.setState(id, version, 'stopped'))}
              >
                {spin('all')}
                Stop everything
              </Button>
            </div>
          </>
        ) : (
          <>
            {state !== 'active' && (
              <>
                <p className="text-[0.9375rem] leading-snug">
                  {live
                    ? `Active, ${name} sends its orders to Upstox with real money.`
                    : `Active, ${name} sends its orders to the Sandbox. No real order is placed.`}
                </p>
                {live && phraseBox}
                <Button
                  className="h-12 text-base"
                  disabled={sending !== null || (live && !phrase.trim())}
                  onClick={() =>
                    send('start', () =>
                      automationApi.setState(id, version, 'active', live ? phrase : undefined)
                    )
                  }
                >
                  {spin('start') || <Play aria-hidden="true" className="size-5" />}
                  Start
                </Button>
              </>
            )}
            {state === 'active' && (
              <>
                <p className="text-[0.9375rem] leading-snug">
                  No new position is opened. Its exit alert still closes the position
                  {held ? ` it holds, ${held}` : ''}.
                </p>
                <Button
                  className="h-12 text-base"
                  disabled={sending !== null}
                  onClick={() =>
                    send('entries', () => automationApi.setState(id, version, 'exits_only'))
                  }
                >
                  {spin('entries') || <LogOut aria-hidden="true" className="size-5" />}
                  Stop new entries
                </Button>
              </>
            )}
            {state !== 'stopped' && (
              <Button
                variant="outline"
                className="h-12"
                aria-haspopup="dialog"
                disabled={sending !== null}
                onClick={() => setAsking(true)}
              >
                <Pause aria-hidden="true" className="size-5" />
                Stop everything, exits too
              </Button>
            )}
            <section
              aria-label="Where its orders go"
              className="flex flex-col gap-3 border-t border-[color:var(--divider)] pt-4"
            >
              <h3 className="text-[1rem] font-bold">Where its orders go</h3>
              {live ? (
                <Button
                  variant="outline"
                  className="h-12"
                  disabled={sending !== null}
                  onClick={() => send('mode', () => automationApi.setMode(id, version, 'sandbox'))}
                >
                  {spin('mode') || <FlaskConical aria-hidden="true" className="size-5" />}
                  Set to Sandbox
                </Button>
              ) : (
                <>
                  {list.live_allowed ? (
                    phraseBox
                  ) : (
                    <p
                      data-testid="auto-live-blocked"
                      className="flex items-start gap-2 text-[0.875rem] font-semibold text-[color:var(--warning-text)]"
                    >
                      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                      {list.live_blocked_why ?? 'Live is not available on this server yet.'} {name}{' '}
                      stays in the Sandbox.
                    </p>
                  )}
                  <Button
                    variant="outline"
                    className="h-12"
                    disabled={sending !== null || !list.live_allowed || !phrase.trim()}
                    onClick={() =>
                      send('mode', () => automationApi.setMode(id, version, 'live', phrase))
                    }
                  >
                    {spin('mode') || <Radio aria-hidden="true" className="size-5" />}
                    Set to Live
                  </Button>
                </>
              )}
            </section>
          </>
        )}
        {refusal && (
          <p role="alert" className="sd-risk-note">
            <TriangleAlert aria-hidden="true" className="size-5 shrink-0" />
            {refusal}
          </p>
        )}
      </div>
    </BottomSheet>
  )
}
