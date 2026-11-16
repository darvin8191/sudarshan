// SUDARSHAN M9.16 (GA7.5): a run's results as a CSV file, through the exports' own save.
import { Download, LoaderCircle, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { OUTLINED } from '@/sudarshan/books/cards/parts'
import { saveDownload } from '@/sudarshan/portfolio/api'
import { problemOf, screenerApi } from './api'

/**
 * "Download CSV" under a run's rows: the service makes the file from the run's own match set and
 * names it in ASCII; the phone keeps it as every SUDARSHAN export is kept. The prices in it are
 * the run's, and the button says so. A file that could not be made is said in the service's
 * words, and the button asks again.
 */
export function RunCsv({ runId }: { runId: number }) {
  const [state, setState] = useState<'idle' | 'making'>('idle')
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  // biome-ignore lint/correctness/useExhaustiveDependencies: another run is another file
  useEffect(() => {
    setProblem(null)
    setSaved(null)
  }, [runId])

  const download = async () => {
    if (state === 'making') return
    setState('making')
    setProblem(null)
    try {
      const file = await screenerApi.runCsv(runId)
      if (!alive.current) return
      saveDownload(file)
      setSaved(file.filename)
    } catch (error) {
      if (alive.current) setProblem(problemOf(error).message)
    } finally {
      if (alive.current) setState('idle')
    }
  }

  return (
    <div className="flex flex-col gap-1.5" data-run-csv={runId}>
      <button
        type="button"
        disabled={state === 'making'}
        onClick={() => void download()}
        className={OUTLINED}
      >
        {state === 'making' ? (
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        ) : (
          <Download aria-hidden="true" className="size-4" />
        )}
        {state === 'making' ? 'Making the file' : 'Download CSV'}
      </button>
      <p className="px-1 text-xs font-medium text-[color:var(--text-tertiary)]">
        {saved
          ? `File: ${saved}. Prices in it are from the run.`
          : 'This run’s rows with the prices at the run, not live prices.'}
      </p>
      {problem && (
        <p role="alert" className="flex items-start gap-1.5 px-1 text-sm font-semibold">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
          />
          {problem}
        </p>
      )}
    </div>
  )
}
