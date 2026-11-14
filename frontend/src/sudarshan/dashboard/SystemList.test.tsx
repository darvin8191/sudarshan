/**
 * Shortcuts and System in place of upstream's Quick Access (design 3.1, D7): a tile per page the
 * bottom bar does not hold, and the platform's pages folded under System, the master contract's
 * state in words among them and About in place of upstream's documentation link.
 */
import { describe, expect, it } from 'vitest'
import { render, screen, userEvent, within } from '@/test/test-utils'
import { SHORTCUTS, Shortcuts } from './Shortcuts'
import { contractLine, SystemList } from './SystemList'

describe('the shortcuts', () => {
  it('give a 48dp tile with an icon and a name to each page, and none to a page not built yet', () => {
    render(<Shortcuts />)
    const tiles = within(screen.getByRole('region', { name: 'Shortcuts' })).getAllByRole('link')
    expect(tiles.map((tile) => [tile.textContent, tile.getAttribute('href')])).toEqual([
      ['Chart', '/chart'],
      ['Screener', '/screener'],
      ['Journal', '/journal'],
      ['Reports', '/reports'],
      ['Tools', '/tools'],
    ])
    for (const tile of tiles) expect(tile).toHaveClass('min-h-16')
    expect(SHORTCUTS.map((each) => each.href)).not.toContain('/options') // arrives with its page
  })
})

describe('the System list', () => {
  it('is folded until asked for, then lists the platform pages with what each holds', async () => {
    const user = userEvent.setup()
    render(<SystemList contract={{ status: 'success', total_symbols: 120345 }} />)
    const toggle = screen.getByRole('button', { name: 'System' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('link')).toBeNull()

    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const rows = screen.getAllByRole('link')
    expect(rows.map((row) => row.getAttribute('href'))).toEqual([
      '/master-contract',
      '/pnl-tracker',
      '/logs',
      '/logs/latency',
      '/search',
      '/about',
    ])
    expect(rows[0]).toHaveTextContent('Master contractReady · 1,20,345 symbols')
    for (const row of rows) expect(row.textContent).not.toMatch(/real-time|live|docs/i)
  })

  it('says the master contract state in words', () => {
    expect(contractLine({ status: 'success' })).toBe('Ready')
    expect(contractLine({ status: 'downloading' })).toBe('Downloading')
    expect(contractLine({ status: 'error', message: 'timeout' })).toBe(
      'Download failed · open it to try again'
    )
    expect(contractLine({ status: 'pending' })).toBe('Checking')
  })
})
