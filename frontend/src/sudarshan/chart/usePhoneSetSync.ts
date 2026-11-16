/**
 * The phone chart's indicator set (M4.2) settled with the server's copy of record (M4.18), as
 * the `phone_set` document `{ version: 1, studies }`. The set on this phone stays the one the
 * chart uses (`prefs.ts`); with the server's switch off, or no answer, nothing else happens.
 *
 * Read once when the chart screen opens; sent after each change the owner makes, which the page
 * reports through the function this returns. A set from another device is put on this phone
 * through `apply`, checked by the same rules as a set read from this phone's storage.
 */
import { useCallback, useEffect, useRef } from 'react'
import { documentSync } from '@/sudarshan/charts/docSync'
import type { StudyEntry } from './phoneStudies'
import { readStudies, saveStudies } from './prefs'

const setText = (set: readonly StudyEntry[]) => JSON.stringify({ version: 1, studies: set })

/** A set sent by the server, by the rules a set read from this phone follows; null when not one. */
export function setFromServer(body: unknown): StudyEntry[] | null {
  const doc = body as { version?: unknown; studies?: unknown } | null
  if (doc?.version !== 1 || !Array.isArray(doc.studies)) return null
  const text = JSON.stringify(doc.studies)
  return readStudies({ getItem: () => text, setItem: () => {} })
}

/** Settle the set with the server's; the function the page calls after each change it saves. */
export function usePhoneSetSync(apply: (set: StudyEntry[]) => void): () => void {
  const applyRef = useRef(apply)
  applyRef.current = apply
  useEffect(() => {
    void documentSync('phone_set', 'phone')
      .bind({
        what: 'Your indicators',
        read: () => setText(readStudies()),
        meaningful: (text) => (setFromServer(JSON.parse(text))?.length ?? 0) > 0,
        adopt: (body) => {
          const set = setFromServer(body)
          if (set === null) return null
          saveStudies(set)
          applyRef.current(set)
          return setText(readStudies()) // what this phone holds now, blocked storage included
        },
      })
      .pull()
  }, [])
  return useCallback(() => documentSync('phone_set', 'phone').changed(), [])
}
