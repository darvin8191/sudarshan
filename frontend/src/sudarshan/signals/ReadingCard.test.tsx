/**
 * M13.7 (U2): one card per (module, index), and the header line. A card: the label, the bias as
 * arrow and word (never colour alone), the confidence as a figure and a bar, "Since 11 Nov, 3
 * sessions", the levels, TRINETRA's word on its data, "Lapsed 09:15", and "Why". The header:
 * section 3.6's words with the age always in them; "Real-time" only while this screen is in the
 * owner's room and the readings are fresh. Readings are the real service's (bridge.answers.json).
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { LATER_MS, NOW_MS, stateOf } from '@/test/signalsAnswers'
import { BiasMark } from './BiasMark'
import { FreshnessLine, realTime } from './FreshnessLine'
import { ReadingCard } from './ReadingCard'
import { freshnessAt } from './words'

const fresh = freshnessAt(stateOf().freshness, NOW_MS)
const stale = freshnessAt(stateOf('state_stale').freshness, LATER_MS)

function readingOf(module: string, instrument: string, name: 'state' | 'state_stale' = 'state') {
  const found = stateOf(name).readings.find(
    (each) => each.module === module && each.instrument === instrument
  )
  if (!found) throw new Error(`no ${module} ${instrument} in the recording`)
  return found
}

describe('a reading card', () => {
  it('DRISHTI · NIFTY: everything TRINETRA published, and Why', async () => {
    const onWhy = vi.fn()
    const reading = readingOf('regime', 'NIFTY')
    render(<ReadingCard reading={reading} fresh={fresh} now={NOW_MS} onWhy={onWhy} />)
    const card = screen.getByRole('article', { name: 'DRISHTI, NIFTY' })
    expect(card).toHaveTextContent('DRISHTI · NIFTY')
    expect(card).toHaveTextContent('swing')
    expect(card).toHaveTextContent('FII distribution')
    expect(card).toHaveTextContent('bearishConfidence 0.71')
    expect(card).toHaveTextContent('Since 09:04, 1 session')
    expect(card).toHaveTextContent('Trigger25,900Invalid at26,120')
    expect(card).not.toHaveTextContent('Target')
    expect(card).not.toHaveTextContent('DISTRIBUTION') // TRINETRA's own state name stays inside
    const bar = card.querySelector('[aria-hidden="true"] > span') as HTMLElement
    expect(bar.style.width).toBe('71%')
    await userEvent.click(within(card).getByRole('button', { name: 'Why: DRISHTI, NIFTY' }))
    expect(onWhy).toHaveBeenCalledWith(reading)
  })

  it("a swing module's figure is its probability; TRINETRA's word on degraded data is said", () => {
    render(
      <ReadingCard
        reading={readingOf('swing_intraday', 'BANKNIFTY')}
        fresh={fresh}
        now={NOW_MS}
        onWhy={() => {}}
      />
    )
    const card = screen.getByRole('article', { name: 'SANDHI intraday, BANKNIFTY' })
    expect(card).toHaveTextContent('Probability 0.66')
    expect(card).toHaveTextContent('TRINETRA marks this reading as resting on incomplete data.')
  })

  it('a module this release does not name is "Other"; with no confidence, no figure and no bar', () => {
    render(
      <ReadingCard
        reading={readingOf('breadth', 'NIFTY')}
        fresh={fresh}
        now={NOW_MS}
        onWhy={() => {}}
      />
    )
    const card = screen.getByRole('article', { name: 'Other, NIFTY' })
    expect(card).not.toHaveTextContent('Confidence')
    expect(card.querySelector('.rounded-full.bg-\\[color\\:var\\(--divider\\)\\]')).toBeNull()
  })

  it('lapsed at 09:15: said, and its colour gone', () => {
    const reading = readingOf('gap_bias', 'NIFTY', 'state_stale')
    render(<ReadingCard reading={reading} fresh={stale} now={LATER_MS} onWhy={() => {}} />)
    const card = screen.getByRole('article', { name: 'PRABHAT, NIFTY' })
    expect(card).toHaveAttribute('data-lapsed', 'true')
    expect(card).toHaveTextContent('Lapsed 09:15')
    expect((card.querySelector('[data-bias]') as HTMLElement).className).toContain(
      '--text-secondary'
    )
  })

  it('stale: grey edge and clock, the bias still coloured under 30 minutes', () => {
    render(
      <ReadingCard
        reading={readingOf('regime', 'NIFTY', 'state_stale')}
        fresh={stale}
        now={LATER_MS}
        onWhy={() => {}}
      />
    )
    const card = screen.getByRole('article', { name: 'DRISHTI, NIFTY' })
    expect(card.className).toContain('--border-strong')
    expect(card.querySelector('.lucide-clock')).not.toBeNull()
    expect((card.querySelector('[data-bias]') as HTMLElement).className).toContain('--down')
  })
})

describe('a bias, told three ways', () => {
  it.each([
    ['bull', 'bullish', 'up', '--up'],
    ['bear', 'bearish', 'down', '--down'],
    ['neutral', 'neutral', 'flat', '--text-primary'],
  ] as const)('%s: its word, its arrow and its token', (bias, word, direction, token) => {
    const { container } = render(<BiasMark bias={bias} colour />)
    const mark = container.firstElementChild as HTMLElement
    expect(mark).toHaveTextContent(word)
    expect(mark.querySelector(`[data-direction="${direction}"]`)).not.toBeNull()
    expect(mark.className).toContain(token)
  })

  it('without colour, the word and arrow in ink-2', () => {
    const { container } = render(<BiasMark bias="bull" colour={false} />)
    const mark = container.firstElementChild as HTMLElement
    expect(mark.className).not.toContain('--up')
    expect(mark).toHaveTextContent('bullish')
  })
})

describe('the header line', () => {
  it('fresh and in the room: the words with the age, and Real-time', () => {
    render(<FreshnessLine fresh={fresh} hearing="joined" connected />)
    const line = screen.getByTestId('trinetra-freshness')
    expect(line).toHaveTextContent('TRINETRA · updated 09:04:40 · under a minute old')
    expect(line.querySelector('[data-updates="real-time"]')).toHaveTextContent('Real-time')
    expect(line).not.toHaveTextContent('Live')
  })

  it('never Real-time while not in the room, or once stale', () => {
    expect(realTime(fresh, 'not_joined')).toBe(false)
    expect(realTime(fresh, 'unknown')).toBe(false)
    expect(realTime(stale, 'joined')).toBe(false)
    render(<FreshnessLine fresh={stale} hearing="joined" connected />)
    const line = screen.getByTestId('trinetra-freshness')
    expect(line).toHaveAttribute('data-fresh', 'stale')
    expect(line.querySelector('[data-updates]')).toBeNull()
    expect(line.className).not.toContain('--live')
  })

  it('out of the room with a connection to hear on: reconnecting, said once', () => {
    render(<FreshnessLine fresh={fresh} hearing="not_joined" connected />)
    expect(screen.getByText(/Reconnecting: a new reading shows here/)).toBeInTheDocument()
  })
})
