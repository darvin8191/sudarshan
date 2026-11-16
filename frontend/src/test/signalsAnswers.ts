/**
 * SUDARSHAN M13 (P8 U1): the TRINETRA view's answers as OpenAlgo's real bridge gave them, in front
 * of the real sudarshan-signals reading invented documents at a fixed clock
 * (src/sudarshan/signals/bridge.answers.json, recorded by sudarshan/tests/test_signals_page_answers.py).
 * The page's tests answer from these, so they hold the page to the bridge as built.
 */
import type { vi } from 'vitest'
import recorded from '@/sudarshan/signals/bridge.answers.json'
import type { SignalsView } from '@/sudarshan/signals/live'
import type { State } from '@/sudarshan/signals/types'

export interface Recorded {
  op: string
  args: Record<string, unknown>
  method: string
  path: string
  query: Record<string, string>
  status: number
  answer: {
    status: 'success' | 'error'
    enabled?: boolean
    data?: unknown
    message?: string
    error?: { code: string; message: string; field?: string }
  }
}

export const ANSWERS = recorded.answers as unknown as Record<string, Recorded>
/** 09:05 IST on Tue 17 Nov 2026: the clock the recording read `state` at. */
export const NOW_MS: number = recorded.now_ms
/** 09:20 the same day: past the last document's next_by and the gap bias's 09:15. */
export const LATER_MS: number = recorded.later_ms

/** A fresh copy of a recorded answer's data. */
export function dataOf<T>(name: string): T {
  return structuredClone(ANSWERS[name].answer.data) as T
}

/** The recorded state, fresh at 09:05 (or `state_stale`, at 09:20). */
export function stateOf(name: 'state' | 'state_stale' = 'state'): State {
  return dataOf<State>(name)
}

/** What a screen holds after reading `state` at this phone's `at`. */
export function viewOf(state: State, at: number = state.now_ms): SignalsView {
  return { kind: 'state', state, at }
}

/** An axios-shaped refusal of a request, carrying the bridge's status and body. */
export function refusal(status: number, data: unknown) {
  return Object.assign(new Error(`status ${status}`), {
    isAxiosError: true,
    response: { status, data, request: {} },
  })
}

/**
 * The web client answering each request from the recording: the route, method and query the page
 * builds must be the ones the bridge was asked with, or the request is refused as unknown.
 */
export function answerFromRecording(
  webClient: {
    get: ReturnType<typeof vi.fn>
    request: ReturnType<typeof vi.fn>
  },
  pick: (method: string, path: string, query: Record<string, string>) => Recorded | undefined
) {
  const reply = (entry: Recorded | undefined) => {
    if (!entry) return Promise.reject(refusal(404, '<html>not here</html>'))
    if (entry.status >= 200 && entry.status < 300) {
      return Promise.resolve({ data: structuredClone(entry.answer), request: {} })
    }
    return Promise.reject(refusal(entry.status, structuredClone(entry.answer)))
  }
  webClient.get.mockImplementation((url: string, config?: { params?: Record<string, string> }) =>
    reply(pick('GET', url, config?.params ?? {}))
  )
  webClient.request.mockImplementation((config: { url: string; method: string }) =>
    reply(pick(config.method, config.url, {}))
  )
}

/** The recorded entry for a request, by route, method and query. */
export function findRecorded(
  method: string,
  path: string,
  query: Record<string, string>,
  names: readonly string[] = Object.keys(ANSWERS)
): Recorded | undefined {
  const same = (a: Record<string, string>, b: Record<string, string>) =>
    JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort())
  return names
    .map((name) => ANSWERS[name])
    .find((entry) => entry.method === method && entry.path === path && same(entry.query, query))
}
