import { useCallback, useEffect, useRef, useState } from 'react'
import { problemOf, type ScreenerProblem, screenerApi } from './api'
import type { Preview, ScreenSpec } from './types'

/** How long the owner stops editing before the screen is checked. */
export const CHECK_AFTER_MS = 1_500

export type CheckState =
  | { status: 'idle' }
  | { status: 'checking' }
  | { status: 'ok'; preview: Preview }
  | { status: 'problem'; problem: ScreenerProblem }

const IDLE: CheckState = { status: 'idle' }

/**
 * The service's own words for a screen as it is edited (design 5.2): the draft is sent to the
 * preview op once the owner has stopped editing for `CHECK_AFTER_MS`, one request at a time, and
 * what comes back is a count and its matches or a sentence for the part that is wrong. The
 * preview is limited to six a minute: a refusal for that is shown as the service worded it and
 * is not tried again until the screen changes. `now` checks at once, for "Test on the last
 * candle". An answer belongs to the draft it was asked for: once the draft changes, it is no
 * longer shown as the draft's.
 */
export function usePreviewCheck(spec: ScreenSpec, enabled: boolean) {
  const [held, setHeld] = useState<{ key: string; state: CheckState } | null>(null)
  const key = JSON.stringify(spec)
  const inFlight = useRef<AbortController | null>(null)
  const latest = useRef({ spec, key })
  latest.current = { spec, key }

  const check = useCallback(async () => {
    const { spec: body, key: bodyKey } = latest.current
    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller
    setHeld({ key: bodyKey, state: { status: 'checking' } })
    try {
      const preview = await screenerApi.preview({ spec: body }, controller.signal)
      if (!controller.signal.aborted) setHeld({ key: bodyKey, state: { status: 'ok', preview } })
    } catch (error) {
      if (controller.signal.aborted) return
      setHeld({ key: bodyKey, state: { status: 'problem', problem: problemOf(error) } })
    }
  }, [])

  const asked = held?.key === key
  useEffect(() => {
    if (!enabled || asked) return
    const timer = setTimeout(() => void check(), CHECK_AFTER_MS)
    return () => clearTimeout(timer)
  }, [enabled, asked, check])

  useEffect(() => () => inFlight.current?.abort(), [])

  const now = useCallback(() => {
    if (held?.key !== latest.current.key) void check()
  }, [held, check])

  return { state: asked && held ? held.state : IDLE, now }
}
