/**
 * The phone chart's indicator set settled with the server's copy (M4.18): a set from another
 * device is put on the phone by the same rules as a stored one, a change made here is sent, a
 * conflict is said, and switched off nothing is asked. A fake server stands in for the bridge.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DocKind, ServerDoc, WriteResult } from '@/sudarshan/charts/api'
import { sharedChartsApi } from '@/sudarshan/charts/api'
import { chartSyncNotices, forgetDocumentSyncs } from '@/sudarshan/charts/docSync'
import { MAX_STUDIES, type StudyEntry } from './phoneStudies'
import { readStudies, saveStudies } from './prefs'
import { setFromServer, usePhoneSetSync } from './usePhoneSetSync'

const docs = new Map<string, { revision: number; body: unknown }>()
const writes: Array<{ base: number; body: { studies: StudyEntry[] } }> = []
let on = true
let next = 1_794_000_000_000

const settle = () => act(async () => {})

beforeEach(() => {
  localStorage.clear()
  forgetDocumentSyncs()
  docs.clear()
  writes.length = 0
  on = true
  vi.spyOn(sharedChartsApi, 'enabled').mockImplementation(async () => on)
  vi.spyOn(sharedChartsApi, 'read').mockImplementation(async (kind: DocKind, key, have) => {
    const doc = docs.get(key)
    if (!doc) return { ok: true, doc: null }
    const out: ServerDoc = { kind, key, revision: doc.revision, updated_ms: 1 }
    if (have !== doc.revision) out.body = structuredClone(doc.body)
    return { ok: true, doc: out }
  })
  vi.spyOn(sharedChartsApi, 'write').mockImplementation(
    async (kind: DocKind, key, base, body): Promise<WriteResult> => {
      writes.push({ base, body: structuredClone(body) as never })
      const doc = docs.get(key)
      if ((doc?.revision ?? 0) !== base)
        return {
          ok: false,
          stale: doc ? { kind, key, revision: doc.revision, updated_ms: 1, body: doc.body } : null,
        }
      docs.set(key, { revision: ++next, body: structuredClone(body) })
      return { ok: true, doc: { kind, key, revision: next, updated_ms: 1 } }
    }
  )
})
afterEach(() => {
  vi.restoreAllMocks()
  for (let n = chartSyncNotices.latest(); n; n = chartSyncNotices.latest())
    chartSyncNotices.clear(n.id)
})

const EMA = { id: 'ema', settings: { length: 21 } }
const RSI = { id: 'rsi', settings: {} }

describe('a set from the server', () => {
  it('is read by the rules a set on this phone follows', () => {
    expect(setFromServer({ version: 1, studies: [EMA, { id: '', settings: {} }, RSI] })).toEqual([
      EMA,
      RSI,
    ])
    const long = Array.from({ length: MAX_STUDIES + 2 }, (_, i) => ({ id: `s${i}`, settings: {} }))
    expect(setFromServer({ version: 1, studies: long })).toHaveLength(MAX_STUDIES)
    expect(setFromServer({ version: 2, studies: [EMA] })).toBeNull()
    expect(setFromServer({ version: 1 })).toBeNull()
    expect(setFromServer(null)).toBeNull()
  })
})

describe('the set on the chart screen', () => {
  it('switched off, asks nothing', async () => {
    on = false
    saveStudies([EMA])
    const apply = vi.fn()
    renderHook(() => usePhoneSetSync(apply))
    await settle()
    expect(sharedChartsApi.read).not.toHaveBeenCalled()
    expect(writes).toEqual([])
    expect(apply).not.toHaveBeenCalled()
  })

  it('puts the set saved on another device on this phone when the screen opens', async () => {
    docs.set('phone', { revision: ++next, body: { version: 1, studies: [RSI] } })
    const apply = vi.fn()
    renderHook(() => usePhoneSetSync(apply))
    await settle()
    expect(apply).toHaveBeenCalledWith([RSI])
    expect(readStudies()).toEqual([RSI])
  })

  it('sends a change made here after the page saves it', async () => {
    const apply = vi.fn()
    const { result } = renderHook(() => usePhoneSetSync(apply))
    await settle()
    saveStudies([EMA])
    result.current()
    await settle()
    expect(writes).toEqual([{ base: 0, body: { version: 1, studies: [EMA] } }])
  })

  it('in a conflict shows the other device’s set and says so', async () => {
    saveStudies([EMA])
    const { result } = renderHook(() => usePhoneSetSync(vi.fn()))
    await settle() // the phone's set is sent first
    docs.set('phone', { revision: ++next, body: { version: 1, studies: [RSI] } })
    saveStudies([EMA, RSI])
    result.current()
    await settle()
    expect(readStudies()).toEqual([RSI])
    expect(chartSyncNotices.latest()?.text).toBe(
      'Your indicators were changed on another device. That copy is shown; the change made on this phone was not saved.'
    )
  })
})
