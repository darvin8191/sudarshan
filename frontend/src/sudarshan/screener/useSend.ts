import { useCallback, useRef, useState } from 'react'
import { problemOf, type ScreenerProblem } from './api'

/**
 * Sends one request at a time from a sheet or a form. A second tap while one is out does nothing
 * (the ref answers at once, before React redraws), and a failure is kept as the sentence the
 * server wrote, to show in place. `run` resolves true when the request succeeded.
 */
export function useSend() {
  const busy = useRef(false)
  /** The problem of the last request that failed, readable the moment `run` resolves. */
  const last = useRef<ScreenerProblem | null>(null)
  const [pending, setPending] = useState(false)
  const [problem, setProblem] = useState<ScreenerProblem | null>(null)

  const run = useCallback(async (send: () => Promise<unknown>): Promise<boolean> => {
    if (busy.current) return false
    busy.current = true
    setPending(true)
    setProblem(null)
    last.current = null
    try {
      await send()
      return true
    } catch (error) {
      last.current = problemOf(error)
      setProblem(last.current)
      return false
    } finally {
      busy.current = false
      setPending(false)
    }
  }, [])

  const clear = useCallback(() => setProblem(null), [])
  return { run, pending, problem, clear, last }
}
