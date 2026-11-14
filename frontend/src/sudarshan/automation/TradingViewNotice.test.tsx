import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import { TradingViewNotice } from './TradingViewNotice'

describe('the TradingView page points strategies to Automation (M12.1, P5)', () => {
  it('says so in one sentence that is a link to the Automation page', () => {
    render(
      <MemoryRouter>
        <TradingViewNotice />
      </MemoryRouter>
    )
    const link = screen.getByRole('link', { name: /For a TradingView strategy, use Automation/ })
    expect(link).toHaveAttribute('href', '/automation')
    expect(link.textContent).not.toMatch(/\barm|paper/i)
  })

  it('is shown on the TradingView page, above its form', () => {
    const page = readFileSync(join(process.cwd(), 'src', 'pages', 'TradingView.tsx'), 'utf8')
    expect(page).toContain(
      "import { TradingViewNotice } from '@/sudarshan/automation/TradingViewNotice'"
    )
    expect(page.indexOf('<TradingViewNotice />')).toBeGreaterThan(page.indexOf('{/* Header */}'))
    expect(page.indexOf('<TradingViewNotice />')).toBeLessThan(page.indexOf('{/* Left Column'))
  })
})
