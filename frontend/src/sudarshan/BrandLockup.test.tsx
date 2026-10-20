import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BrandLockup } from './BrandLockup'

describe('BrandLockup', () => {
  it.each(['bar', 'stacked'] as const)('names the product in both scripts (%s)', (variant) => {
    const { container } = render(<BrandLockup variant={variant} />)

    expect(screen.getByText('सुदर्शन')).toHaveAttribute('lang', 'sa')
    expect(screen.getByText('SUDARSHAN')).toBeInTheDocument()
    expect(container.firstElementChild).toHaveTextContent('सुदर्शनSUDARSHAN')
  })

  it('keeps the app-bar hairline out of the accessible text', () => {
    const { container } = render(<BrandLockup />)

    expect(container.querySelector('.sd-lockup__rule')).toHaveAttribute('aria-hidden', 'true')
  })
})
