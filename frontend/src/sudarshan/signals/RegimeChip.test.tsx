/**
 * M13.4 (U3): the regime chip for the phone chart's header. One line: the index, TRINETRA's label,
 * the bias as arrow and word, the confidence, and the reading's age; it opens the TRINETRA page.
 * Nothing at all for another instrument (and no request), while the view is off or unanswered, or
 * when TRINETRA never published. Stale: grey edge and clock; past 30 minutes no colour of bias.
 */
import { screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const webClient = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))

import { LATER_MS, NOW_MS, stateOf, viewOf } from '@/test/signalsAnswers'
import { renderSignals } from '@/test/signalsRender'
import { resetSignalsLiveForTests } from './live'
import { RegimeChip, regimeInstrument } from './RegimeChip'
import type { State } from './types'

function at(now: number) {
  vi.useFakeTimers({ now, toFake: ['Date'] })
}

beforeEach(() => {
  resetSignalsLiveForTests()
  webClient.get.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('the regime chip', () => {
  it('for a chart of neither index: nothing, and nothing is read', () => {
    const { container } = renderSignals(<RegimeChip instrument={regimeInstrument('INFY', 'NSE')} />)
    expect(container).toBeEmptyDOMElement()
    expect(webClient.get).not.toHaveBeenCalled()
  })

  it('NIFTY at 09:05: label, arrow and word, confidence and age, opening the page', () => {
    at(NOW_MS)
    renderSignals(<RegimeChip instrument="NIFTY" />, { view: viewOf(stateOf()) })
    const chip = screen.getByTestId('regime-chip')
    expect(chip).toHaveAttribute('href', '/trinetra')
    expect(chip).toHaveAttribute('data-fresh', 'fresh')
    expect(chip).toHaveTextContent('NIFTY·FII distributionbearish0.71')
    expect(chip).toHaveTextContent('<1 min old')
    const bias = chip.querySelector('[data-bias="bear"]') as HTMLElement
    expect(bias.className).toContain('--down')
    expect(bias.querySelector('[data-direction="down"]')).not.toBeNull()
    expect(chip.getAttribute('aria-label')).toContain('TRINETRA · updated 09:04:40')
    expect(chip.querySelector('.lucide-clock')).toBeNull()
    expect(chip.textContent).not.toMatch(/Real-time|Live/)
    expect(chip.className).toContain('min-h-12')
  })

  it('a contract on BANKNIFTY reads the BANKNIFTY regime: neutral, in ink, with its own arrow', () => {
    at(NOW_MS)
    renderSignals(<RegimeChip instrument={regimeInstrument('BANKNIFTY25NOV2658000CE', 'NFO')} />, {
      view: viewOf(stateOf()),
    })
    const chip = screen.getByTestId('regime-chip')
    expect(chip).toHaveTextContent('BANKNIFTY·Range-boundneutral0.55')
    const bias = chip.querySelector('[data-bias="neutral"]') as HTMLElement
    expect(bias.className).toContain('--text-primary')
    expect(bias.querySelector('[data-direction="flat"]')).not.toBeNull()
  })

  it('stale at 09:20: grey edge, clock, the age in warning ink; the colour stays under 30 minutes', () => {
    at(LATER_MS)
    renderSignals(<RegimeChip instrument="NIFTY" />, { view: viewOf(stateOf('state_stale')) })
    const chip = screen.getByTestId('regime-chip')
    expect(chip).toHaveAttribute('data-fresh', 'stale')
    expect(chip.querySelector('.lucide-clock')).not.toBeNull()
    expect(chip.querySelector('.rounded-full')?.className).toContain('--border-strong')
    expect(chip).toHaveTextContent('15 min old')
    expect((chip.querySelector('[data-bias]') as HTMLElement).className).toContain('--down')
  })

  it('stale for 40 minutes: no colour of bias, the word and arrow kept', () => {
    at(LATER_MS + 25 * 60_000)
    // Read at 09:20 (fifteen minutes stale), and the phone's clock has moved on 25 minutes since.
    renderSignals(<RegimeChip instrument="NIFTY" />, { view: viewOf(stateOf('state_stale')) })
    const bias = screen.getByTestId('regime-chip').querySelector('[data-bias]') as HTMLElement
    expect(bias.className).toContain('--text-secondary')
    expect(bias.className).not.toContain('--down')
    expect(bias).toHaveTextContent('bearish')
  })

  it('nothing while the view is off, unanswered, or never heard from', () => {
    at(NOW_MS)
    const off = renderSignals(<RegimeChip instrument="NIFTY" />, {
      view: { kind: 'off', message: 'The TRINETRA view is switched off.', at: NOW_MS },
    })
    expect(off.container).toBeEmptyDOMElement()
    off.unmount()
    const never: State = {
      ...stateOf(),
      readings: [],
      freshness: {
        ...stateOf().freshness,
        status: 'never',
        published_ms: null,
        fresh_until_ms: null,
      },
    }
    const silent = renderSignals(<RegimeChip instrument="NIFTY" />, { view: viewOf(never) })
    expect(silent.container).toBeEmptyDOMElement()
  })
})
