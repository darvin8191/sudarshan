import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TabStrip } from './TabStrip'
import { MAX_TABS, type TabInfo, WORDS } from './types'

function tab(id: number, name: string, icon: TabInfo['icon'] = 'briefcase'): TabInfo {
  return {
    id,
    name,
    name_alt: null,
    kind: 'stocks',
    icon,
    colour: 'brand',
    position: id,
    pinned: false,
    archived: false,
    system: false,
    manual_old_days: 30,
    version: 1,
  }
}

describe('the tab strip', () => {
  it('leads with Upstox, read-only, with a lock; then the tabs in order; then New tab', () => {
    render(
      <TabStrip
        tabs={[tab(1, 'Gold', 'gem'), tab(2, 'Funds', 'coins')]}
        selected={null}
        onSelect={() => {}}
        onNew={() => {}}
      />
    )
    const chips = screen.getAllByRole('tab')
    expect(chips.map((chip) => chip.textContent)).toEqual(['Upstoxread-only', 'Gold', 'Funds'])
    expect(chips[0].querySelector('svg')).not.toBeNull() // the lock
    expect(screen.getByRole('tab', { name: /Upstox.*read-only/ })).toBeTruthy()
    const all = screen.getAllByRole('button')
    expect(all[all.length - 1].textContent).toBe('New tab')
  })

  it('tells the selected tab by more than colour: aria, a 2 px edge and bold text', () => {
    render(<TabStrip tabs={[tab(1, 'Gold')]} selected={1} onSelect={() => {}} onNew={() => {}} />)
    const gold = screen.getByRole('tab', { name: 'Gold' })
    const upstox = screen.getByRole('tab', { name: /Upstox/ })
    expect(gold.getAttribute('aria-selected')).toBe('true')
    expect(upstox.getAttribute('aria-selected')).toBe('false')
    expect(gold.className).toMatch(/border-2/)
    expect(gold.className).toMatch(/font-bold/)
    expect(upstox.className).not.toMatch(/border-2|font-bold/)
  })

  it('every chip is at least 48 dp tall', () => {
    render(
      <TabStrip tabs={[tab(1, 'Gold')]} selected={null} onSelect={() => {}} onNew={() => {}} />
    )
    for (const chip of [
      ...screen.getAllByRole('tab'),
      screen.getByRole('button', { name: 'New tab' }),
    ]) {
      expect(chip.className).toMatch(/min-h-12/)
    }
  })

  it('selects a tab, or Upstox as none, and opens the new-tab sheet', async () => {
    const onSelect = vi.fn()
    const onNew = vi.fn()
    render(<TabStrip tabs={[tab(7, 'Gold')]} selected={7} onSelect={onSelect} onNew={onNew} />)
    await userEvent.click(screen.getByRole('tab', { name: /Upstox/ }))
    await userEvent.click(screen.getByRole('tab', { name: 'Gold' }))
    await userEvent.click(screen.getByRole('button', { name: 'New tab' }))
    expect(onSelect.mock.calls).toEqual([[null], [7]])
    expect(onNew).toHaveBeenCalledTimes(1)
  })

  it('at 50 tabs New tab is off, and the limit is said in words', async () => {
    const onNew = vi.fn()
    const many = Array.from({ length: MAX_TABS }, (_, i) => tab(i + 1, `T${i + 1}`))
    render(<TabStrip tabs={many} selected={null} onSelect={() => {}} onNew={onNew} />)
    const button = screen.getByRole('button', { name: 'New tab' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(screen.getByText(WORDS.tab_limit)).toBeTruthy()
    await userEvent.click(button)
    expect(onNew).not.toHaveBeenCalled()
  })
})
