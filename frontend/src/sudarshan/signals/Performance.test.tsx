/**
 * M13.8 (U5): the Performance tab against the performance op as built: every row with its number of
 * cases; a judged row with its share and median move; under 30 cases "too few cases to judge" and no
 * figure; a neutral reading's median move with no share. The scores are the real scorer's, through
 * the real bridge (bridge.answers.json: invented changes and closes, 32 cases a bias for the regime
 * and 6 for the gap bias), and "No scores yet" as a fresh service answers.
 */
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const webClient = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient }))

import { ANSWERS, answerFromRecording, refusal } from '@/test/signalsAnswers'
import { renderSignals } from '@/test/signalsRender'
import { MIN_CASES, Performance, rowWords, verdictOf } from './Performance'
import { type PerfRow, WORDS } from './types'

beforeEach(() => {
  webClient.get.mockReset()
})

describe('the Performance tab', () => {
  it('scores per module, index and bias, the cases beside every figure', async () => {
    answerFromRecording(webClient, () => ANSWERS.performance_scored)
    renderSignals(<Performance />)
    const regime = await screen.findByRole('region', { name: 'DRISHTI' })
    const rows = within(regime).getAllByRole('listitem')
    expect(rows).toHaveLength(9)
    expect(rows[0]).toHaveTextContent('NIFTYbullishafter 1 session')
    expect(rows[0]).toHaveTextContent(
      '32 cases · 56.25% moved the way it said · median move +0.18%'
    )
    expect(rows[0]).toHaveAttribute('data-verdict', 'scored')
    expect(rows[3]).toHaveTextContent(
      'bearishafter 1 session32 cases · 43.75% moved the way it said'
    )
    expect(rows[6]).toHaveAttribute('data-verdict', 'no_direction')
    expect(rows[6]).toHaveTextContent(
      '32 cases · median move +0.13% · a neutral reading names no direction to score'
    )
    expect(rows[6]).not.toHaveTextContent('moved the way')
    const gap = screen.getByRole('region', { name: 'PRABHAT' })
    const opens = within(gap).getAllByRole('listitem')
    expect(opens[0]).toHaveTextContent('at the open (the gap)6 cases · too few cases to judge')
    expect(opens[0].textContent).not.toContain('%')
    expect(screen.getByText(/Scored up to 3 Nov 2026\./)).toBeInTheDocument()
    expect(webClient.get.mock.calls[0][0]).toBe('/sudarshan/signals/api/performance')
  })

  it('no scores yet, as a fresh service says', async () => {
    answerFromRecording(webClient, () => ANSWERS.performance)
    renderSignals(<Performance />)
    expect(await screen.findByText(WORDS.no_scores)).toBeInTheDocument()
  })

  it('a refusal in its sentence, with Try again', async () => {
    webClient.get.mockRejectedValue(
      refusal(501, { status: 'error', error: { code: 'not_ready', message: WORDS.not_ready } })
    )
    renderSignals(<Performance />)
    expect(await screen.findByRole('alert')).toHaveTextContent(WORDS.not_ready)
    expect(screen.getByRole('button', { name: 'Try again' })).toHaveClass('min-h-12')
  })

  it('under 30 cases is never judged, whatever the row says', () => {
    const row: PerfRow = {
      module: 'regime',
      instrument: 'NIFTY',
      bias: 'bull',
      sessions: 1,
      cases: MIN_CASES - 1,
      hits: 20,
      hit_pct: '68.97',
      median_move_pct: '0.50',
      judged: true,
      words: null,
    }
    expect(verdictOf(row)).toBe('too_few')
    expect(rowWords(row)).toBe('29 cases · too few cases to judge')
    expect(rowWords({ ...row, cases: 1 })).toBe('1 case · too few cases to judge')
    expect(rowWords({ ...row, cases: 30, median_move_pct: '-0.42' })).toBe(
      '30 cases · 68.97% moved the way it said · median move −0.42%'
    )
  })
})
