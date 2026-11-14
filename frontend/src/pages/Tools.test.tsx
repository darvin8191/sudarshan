/**
 * SUDARSHAN M11.13 (T2): the tools page on tokens: one look for every tile (the brand tint and the
 * tool's icon, no palette colour per tile), and on a phone the wide-screen pages under their own
 * heading. The home page's count still comes from the one registry.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { tools } from '@/lib/tools'
import { fakeScreen } from '@/test/screenWidth'
import { render, screen, within } from '@/test/test-utils'
import Tools from './Tools'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the tools page', () => {
  it('draws every tool once, on the brand tint with its icon, with no palette colour', () => {
    const { container } = render(<Tools />)
    const tiles = screen.getAllByRole('link')
    expect(tiles.map((tile) => tile.getAttribute('href'))).toEqual(tools.map((tool) => tool.href))
    for (const tile of tiles) {
      expect(tile.querySelector('svg')).not.toBeNull()
      expect(tile.innerHTML).toContain('var(--brand-tint)')
    }
    expect(container.innerHTML).not.toMatch(/\bbg-[a-z]+-\d{3}\b/)
    expect(screen.queryByRole('heading', { name: 'Best on a wide screen' })).toBeNull()
  })

  it('on a phone lists the wide-screen pages under their own heading', () => {
    fakeScreen('phone')
    render(<Tools />)
    const wide = screen.getByRole('region', { name: 'Best on a wide screen' })
    expect(within(wide).getAllByRole('link')).toHaveLength(
      tools.filter((tool) => !tool.phone).length
    )
    // Nothing is made for the phone until the phone's option chain lands with its page.
    expect(screen.queryByRole('heading', { name: 'Made for the phone' })).toBeNull()
  })

  it('keeps the registry free of palette colours and of feed words a tile cannot back', () => {
    const source = readFileSync(join(process.cwd(), 'src', 'lib', 'tools.ts'), 'utf8')
    expect(source).not.toMatch(/\bbg-[a-z]+-\d{3}\b/)
    for (const tool of tools) expect(tool.description).not.toMatch(/real-?time|\blive\b/i)
  })
})
