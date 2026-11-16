/**
 * M13.5 (U3): the gap-bias card on the dashboard, from 08:45 until the open at 09:15, and after it
 * until the first change of a gap-bias reading after the open (a lapse at the open counts), never
 * past 15:30; only readings resting on today's data. The readings are the real service's
 * (bridge.answers.json: TRINETRA's gap bias of Tue 17 Nov, lapsing at 09:15).
 */
import { screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({ webClient: { get: vi.fn(), request: vi.fn() } }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))

import { NOW_MS, stateOf, viewOf } from '@/test/signalsAnswers'
import { renderSignals } from '@/test/signalsRender'
import { GapBiasCard, gapRows } from './GapBiasCard'
import { resetSignalsLiveForTests } from './live'
import type { State } from './types'

const DAY0 = 1794853800000 // Tue 17 Nov 2026 00:00 IST
const ist = (hour: number, minute: number, second = 0) =>
  DAY0 + (hour * 3600 + minute * 60 + second) * 1000

const names = (rows: ReturnType<typeof gapRows>) => rows?.map((row) => row.instrument) ?? null

/** The recorded state with its gap-bias readings changed as given. */
function withGap(change: Partial<State['readings'][number]>): State {
  const state = stateOf()
  state.readings = state.readings.map((reading) =>
    reading.module === 'gap_bias' ? { ...reading, ...change } : reading
  )
  return state
}

beforeEach(() => resetSignalsLiveForTests())
afterEach(() => vi.useRealTimers())

describe('when the card shows', () => {
  const state = stateOf()

  it('from 08:45, NIFTY then BANKNIFTY', () => {
    expect(gapRows(state, ist(8, 44, 59))).toBeNull()
    expect(names(gapRows(state, ist(8, 45)))).toEqual(['NIFTY', 'BANKNIFTY'])
    expect(names(gapRows(state, NOW_MS))).toEqual(['NIFTY', 'BANKNIFTY'])
    expect(names(gapRows(state, ist(9, 14, 59)))).toEqual(['NIFTY', 'BANKNIFTY'])
  })

  it('goes at the open when the readings lapse then (their 09:15)', () => {
    expect(gapRows(state, ist(9, 15))).toBeNull()
    expect(gapRows(stateOf('state_stale'), ist(9, 20))).toBeNull()
  })

  it('a reading that holds past the open stays until its first change after it', () => {
    const holding = withGap({ valid_until_ms: null })
    expect(names(gapRows(holding, ist(10, 30)))).toEqual(['NIFTY', 'BANKNIFTY'])
    const changed = withGap({ valid_until_ms: null, since_ms: ist(9, 40) })
    expect(gapRows(changed, ist(10, 30))).toBeNull()
    const noData = withGap({ valid_until_ms: null, quality: 'no_data', as_of_ms: ist(9, 30) })
    expect(gapRows(noData, ist(10, 30))).toBeNull()
  })

  it('never past 15:30', () => {
    expect(gapRows(withGap({ valid_until_ms: null }), ist(15, 30))).toBeNull()
  })

  it("never yesterday's call as today's", () => {
    const yesterday = withGap({ as_of_ms: ist(8, 44) - 86_400_000 })
    expect(gapRows(yesterday, NOW_MS)).toBeNull()
  })
})

describe('the card', () => {
  it('at 09:05: both readings with their bias, until when they hold, and the freshness line', () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date'] })
    renderSignals(<GapBiasCard />, { view: viewOf(stateOf()) })
    const card = screen.getByTestId('sd-trinetra-gap')
    expect(within(card).getByRole('heading', { level: 2 })).toHaveTextContent(
      'PRABHAT · gap bias at the open'
    )
    const rows = within(card).getAllByRole('listitem')
    expect(rows.map((row) => row.getAttribute('data-instrument'))).toEqual(['NIFTY', 'BANKNIFTY'])
    expect(rows[0]).toHaveTextContent('Mild gap downbearishconfidence 0.62Holds until 09:15')
    expect(rows[1]).toHaveTextContent('Flat openneutralconfidence 0.51')
    expect(within(card).getByTestId('trinetra-freshness')).toHaveTextContent(
      'TRINETRA · updated 09:04:40 · under a minute old'
    )
    expect(within(card).getByRole('link', { name: /TRINETRA/ })).toHaveAttribute(
      'href',
      '/trinetra'
    )
  })

  it('nothing before 08:45, and nothing while the view is off', () => {
    vi.useFakeTimers({ now: ist(8, 0), toFake: ['Date'] })
    // The server's clock at 08:00 (the answer read then, on a phone whose clock agrees).
    const early = renderSignals(<GapBiasCard />, {
      view: viewOf({ ...stateOf(), now_ms: ist(8, 0) }),
    })
    expect(early.container).toBeEmptyDOMElement()
    early.unmount()
    const off = renderSignals(<GapBiasCard />, {
      view: { kind: 'off', message: 'The TRINETRA view is switched off.', at: NOW_MS },
    })
    expect(off.container).toBeEmptyDOMElement()
  })
})
