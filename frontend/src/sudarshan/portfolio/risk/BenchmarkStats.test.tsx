/** The benchmark's three figures on Performance (P7 F6), against lane R's two fixture shapes. */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { report } from '../tab/fixtures'
import { Performance } from '../tab/Performance'
import { BenchmarkStats, excessWord, ratioText } from './BenchmarkStats'

const [shown, waiting] = report().benchmarks

describe('the figures', () => {
  it('a ratio shows two decimals with a true minus, and nothing when there is none', () => {
    expect(ratioText('0.8500')).toBe('0.85')
    expect(ratioText('-0.1250')).toBe('−0.13')
    expect(ratioText('-0.0010')).toBe('0.00')
    expect(ratioText(null)).toBeNull()
  })

  it('the excess return has its word', () => {
    expect(excessWord(6.5)).toBe('ahead')
    expect(excessWord(-0.4)).toBe('behind')
    expect(excessWord(0.001)).toBe('level')
  })

  it('shows beta, correlation and the signed excess with its word, then what they rest on', () => {
    render(<BenchmarkStats b={shown} />)
    expect(screen.getByText('0.85')).toBeTruthy()
    expect(screen.getByText('0.72')).toBeTruthy()
    const excess = screen.getByText('+6.50%')
    expect(excess.querySelector('svg')).not.toBeNull() // the arrow, beside the sign and the word
    expect(screen.getByText('ahead')).toBeTruthy()
    expect(screen.getByText('On 60 trading days to 13 Nov 2026')).toBeTruthy()
  })

  it('behind the index reads behind, with a minus', () => {
    render(<BenchmarkStats b={{ ...shown, excess_pct: '-2.2500' }} />)
    expect(screen.getByText('−2.25%')).toBeTruthy()
    expect(screen.getByText('behind')).toBeTruthy()
  })

  it('while waiting shows only the sentence, never a zero', () => {
    const { container } = render(<BenchmarkStats b={waiting} />)
    expect(screen.getByText('Shown after 20 trading days (from 11 Dec 2026)')).toBeTruthy()
    expect(container.querySelector('dl')).toBeNull()
    expect(container.textContent).not.toMatch(/0\.00/)
  })

  it('draws nothing for an answer from before these figures', () => {
    const old = { ...waiting, stat_note: null }
    expect(render(<BenchmarkStats b={old} />).container.innerHTML).toBe('')
  })
})

describe('on Performance', () => {
  it('each index carries its own row, and nothing says Real-time', () => {
    render(<Performance report={report()} />)
    const nifty = screen.getByText('NIFTY 50').closest('li') as HTMLElement
    expect(within(nifty).getByText('Beta')).toBeTruthy()
    expect(within(nifty).getByText('ahead')).toBeTruthy()
    const wide = screen.getByText('NIFTY 500').closest('li') as HTMLElement
    expect(within(wide).queryByText('Beta')).toBeNull()
    expect(within(wide).getByText(/Shown after 20 trading days/)).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/Real-time/)
  })
})
