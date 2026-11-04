import { useCallback, useRef, useState } from 'react'
import { PortfolioError, problemOf } from '../api'

/**
 * Sends one request at a time from a sheet. A second tap while one is out does nothing (the
 * ref answers at once, before React redraws), and a failure is kept as the sentence the server
 * wrote, to show in the sheet. `run` resolves true when the request succeeded.
 */
export function useSend() {
  const busy = useRef(false)
  const [pending, setPending] = useState(false)
  const [problem, setProblem] = useState<{ code: string; message: string } | null>(null)

  const run = useCallback(async (send: () => Promise<unknown>): Promise<boolean> => {
    if (busy.current) return false
    busy.current = true
    setPending(true)
    setProblem(null)
    try {
      await send()
      return true
    } catch (error) {
      const { code, message } = error instanceof PortfolioError ? error : problemOf(error)
      setProblem({ code, message })
      return false
    } finally {
      busy.current = false
      setPending(false)
    }
  }, [])

  const clear = useCallback(() => setProblem(null), [])
  return { run, pending, problem, clear }
}
