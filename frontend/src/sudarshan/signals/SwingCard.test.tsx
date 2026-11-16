/**
 * M13.6 (U3): the swing card on the dashboard: SANDHI's end-of-day and intraday readings for both
 * indices, each with its label, bias and probability; current readings only; nothing without one.
 * The readings are the real service's (bridge.answers.json).
 */
import { screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({ webClient: { get: vi.fn(), request: vi.fn() } }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))

import { LATER_MS, NOW_MS, stateOf, viewOf } from '@/test/signalsAnswers'
import { renderSignals } from '@/test/signalsRender'
import { resetSignalsLiveForTests } from './live'
import { SwingCard, swingRows } from './SwingCard'

beforeEach(() => resetSignalsLiveForTests())
afterEach(() => vi.useRealTimers())

describe('the swing card', () => {
  it('lists the current swing readings, end-of-day first', () => {
    const rows = swingRows(stateOf(), NOW_MS)
    expect(rows.map((row) => `${row.module}:${row.instrument}`)).toEqual([
      'swing_eod:NIFTY',
      'swing_intraday:BANKNIFTY',
    ])
  })

  it("says each with its label, bias and probability, and TRINETRA's word on its data", () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date'] })
    renderSignals(<SwingCard />, { view: viewOf(stateOf()) })
    const card = screen.getByTestId('sd-trinetra-swing')
    expect(within(card).getByRole('heading', { level: 2 })).toHaveTextContent(
      'SANDHI · swing reversal'
    )
    const rows = within(card).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('NIFTY · end-of-daySwing top formingbearishprobability 0.58')
    expect(rows[1]).toHaveTextContent(
      'BANKNIFTY · intradayIntraday reversal upbullishprobability 0.66'
    )
    expect((rows[1].querySelector('[data-bias]') as HTMLElement).className).toContain('--up')
    expect(within(card).getByTestId('trinetra-freshness')).toHaveAttribute('data-fresh', 'fresh')
  })

  it('stale: the card takes the grey edge and the line says so', () => {
    vi.useFakeTimers({ now: LATER_MS, toFake: ['Date'] })
    renderSignals(<SwingCard />, { view: viewOf(stateOf('state_stale')) })
    const card = screen.getByTestId('sd-trinetra-swing')
    expect(card.className).toContain('--border-strong')
    expect(within(card).getByTestId('trinetra-freshness')).toHaveTextContent(
      'TRINETRA has not published since 09:04. This is its last reading, 15 minutes old.'
    )
  })

  it('nothing without a current swing reading', () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date'] })
    const state = stateOf()
    state.readings = state.readings.filter((reading) => !reading.module.startsWith('swing'))
    const { container } = renderSignals(<SwingCard />, { view: viewOf(state) })
    expect(container).toBeEmptyDOMElement()
  })
})
