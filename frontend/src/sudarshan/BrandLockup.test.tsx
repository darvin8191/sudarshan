import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BrandLockup } from './BrandLockup'
import { ICON, WORDMARK } from './marks'

describe('BrandLockup', () => {
  it.each(['bar', 'stacked'] as const)('names the product in both scripts (%s)', (variant) => {
    render(<BrandLockup variant={variant} />)

    const wordmark = screen.getByRole('img', { name: 'सुदर्शन' })
    expect(wordmark).toHaveAttribute('lang', 'sa')
    expect(screen.getByText('SUDARSHAN')).toBeInTheDocument()
  })

  it('draws सुदर्शन from the Tiro outlines, so no font has to load first', () => {
    const { container } = render(<BrandLockup />)

    expect(container.querySelector('.sd-lockup__wordmark path')).toHaveAttribute('d', WORDMARK.d)
    expect(container.querySelector('.sd-lockup__wordmark')).toHaveAttribute(
      'viewBox',
      WORDMARK.viewBox
    )
  })

  it.each([
    'bar',
    'stacked',
  ] as const)('shows the Mor icon, kept out of the accessible text (%s)', (variant) => {
    const { container } = render(<BrandLockup variant={variant} />)

    const icon = container.querySelector('.sd-lockup__icon')
    expect(icon).toHaveAttribute('aria-hidden', 'true')
    expect(icon?.querySelector('rect')).toHaveAttribute('fill', ICON.tile)
    expect(icon?.querySelector('circle')).toHaveAttribute('stroke', ICON.ring.color)
  })

  it('gives a link around it the name in both scripts', () => {
    render(
      <a href="/dashboard">
        <BrandLockup />
      </a>
    )

    expect(screen.getByRole('link', { name: /सुदर्शन\s*SUDARSHAN/ })).toBeInTheDocument()
  })

  it('takes the login size and alignment when stacked', () => {
    const { container } = render(<BrandLockup variant="stacked" size="lg" align="start" />)

    expect(container.firstElementChild).toHaveClass(
      'sd-lockup--stacked',
      'sd-lockup--lg',
      'sd-lockup--start'
    )
  })
})
