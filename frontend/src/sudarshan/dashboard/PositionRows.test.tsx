/**
 * The P&L card's position rows (M3.3, D4; decision 4: five rows, no actions): the order, the cap
 * of five, a row no tick has moved yet, Buy and Sell said in words, and a family member's mask.
 */
import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@/test/test-utils'
import type { PositionRow } from './live'
import { PositionRows } from './PositionRows'

const READ = Date.parse('2026-11-17T10:42:05+05:30')

function row(symbol: string, quantity: number, pnl: number | null, ticking = true): PositionRow {
  return { key: symbol, symbol, exchange: 'NFO', product: 'NRML', quantity, pnl, ticking }
}

describe('the position rows', () => {
  it('lists five, then says how many more and links to Positions; nothing else can be pressed', () => {
    const rows = ['A', 'B', 'C', 'D', 'E', 'F', 'G'].map((name, i) => row(name, 65, 700 - i * 100))
    render(<PositionRows rows={rows} readAt={READ} />)
    const list = screen.getByRole('list', { name: 'Open positions' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(5)
    expect(screen.getByRole('link', { name: '2 more · See all positions' })).toHaveAttribute(
      'href',
      '/positions'
    )
    expect(screen.queryByRole('button')).toBeNull() // Exit, Add and Exit all stay on Positions
    expect(within(list).getAllByRole('link')).toHaveLength(5) // each contract to its chart
    expect(within(list).getAllByRole('link')[0]).toHaveAttribute('title', 'Open the chart for A')
  })

  it('says long and short by sign and arrow, and Buy or Sell in words, never by colour alone', () => {
    render(
      <PositionRows
        rows={[row('NIFTY17NOV2625900CE', 65, 6110.25), row('BANKNIFTY29DEC26FUT', -30, -1020)]}
        readAt={READ}
      />
    )
    const [long, short] = screen.getAllByRole('listitem')
    expect(long).toHaveTextContent('NIFTY 25,900 CE, 17 Nov · +65 · NRML, Buy')
    expect(long).toHaveTextContent('+₹6,110.25')
    expect(long.querySelector('[data-direction="up"]')).not.toBeNull()
    expect(short).toHaveTextContent('BANKNIFTY FUT, 29 Dec · −30 · NRML, Sell')
    expect(short).toHaveTextContent('−₹1,020.00')
    expect(short.querySelector('[data-direction="down"]')).not.toBeNull()
    expect(screen.queryByText(/No tick yet/)).toBeNull()
  })

  it("marks a row no tick has moved with a clock and gives the broker's read time", () => {
    render(<PositionRows rows={[row('QUIET', 65, 650, false)]} readAt={READ} />)
    const [quiet] = screen.getAllByRole('listitem')
    expect(quiet).toHaveTextContent('+₹650.00 as the broker read it')
    expect(quiet.querySelector('[data-direction]')).toBeNull()
    expect(
      screen.getByText('No tick yet on rows with a clock · broker read 10:42:05')
    ).toBeVisible()
  })

  it("shows Hidden where a family member's mask hides the quantity or the P&L", () => {
    render(
      <PositionRows
        rows={[row('MASKED', 65, 650)]}
        readAt={READ}
        mask={{ pnl: true, quantity: true }}
      />
    )
    const [masked] = screen.getAllByRole('listitem')
    expect(masked).toHaveTextContent('MASKED, Hidden · NRML')
    expect(within(masked).getByText('Hidden')).toBeVisible() // the P&L's own cell
    expect(masked).not.toHaveTextContent(/650|\+65|Buy/)
  })

  it('draws nothing when no position is open', () => {
    const { container } = render(<PositionRows rows={[]} readAt={READ} />)
    expect(container).toBeEmptyDOMElement()
  })
})
