import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useThemeStore } from '@/stores/themeStore'
import { AnalyzeBanner } from './AnalyzeBanner'

afterEach(() => {
  useThemeStore.setState({ appMode: 'live' })
})

describe('Analyze banner', () => {
  it('is absent in Live mode', () => {
    useThemeStore.setState({ appMode: 'live' })
    render(<AnalyzeBanner />)

    expect(screen.queryByTestId('analyze-banner')).toBeNull()
  })

  it('names the mode in words and an icon, never in colour alone', () => {
    useThemeStore.setState({ appMode: 'analyzer' })
    render(<AnalyzeBanner />)

    const banner = screen.getByRole('status')
    expect(banner).toHaveTextContent('ANALYZE')
    expect(banner).toHaveTextContent('સેન્ડબોક્સ')
    expect(banner.querySelector('[lang="gu"]')).toHaveTextContent('સેન્ડબોક્સ')
    expect(banner.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })
})
