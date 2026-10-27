import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DisplaySettings } from './DisplaySettings'
import { setCvdMode } from './display'

const html = document.documentElement

afterEach(() => {
  vi.restoreAllMocks()
  setCvdMode(false)
  localStorage.clear()
})

describe('Display settings', () => {
  it('states the light theme and the brand accent instead of offering them', () => {
    render(<DisplaySettings />)

    expect(screen.getByText('Light. There is no dark theme.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /dark/i })).toBeNull()
    expect(screen.queryByRole('radio')).toBeNull()
  })

  it('turns colour-blind mode on and off for this device', async () => {
    const user = userEvent.setup()
    render(<DisplaySettings />)
    const toggle = screen.getByRole('switch', { name: 'Colour-blind mode' })
    expect(toggle).not.toBeChecked()

    await user.click(toggle)
    expect(toggle).toBeChecked()
    expect(html.getAttribute('data-cvd')).toBe('1')
    expect(localStorage.getItem('sudarshan-cvd')).toBe('1')

    await user.click(toggle)
    expect(toggle).not.toBeChecked()
    expect(html.hasAttribute('data-cvd')).toBe(false)
    expect(localStorage.getItem('sudarshan-cvd')).toBeNull()
  })

  // SUDARSHAN (M6.8): the order alert sound, muted per device beside colour-blind mode.
  it('turns the order alert sound off and on for this device', async () => {
    const user = userEvent.setup()
    render(<DisplaySettings />)
    const toggle = screen.getByRole('switch', { name: 'Order alert sound' })
    expect(toggle).toBeChecked()

    await user.click(toggle)
    expect(toggle).not.toBeChecked()
    expect(localStorage.getItem('sudarshan-order-sound')).toBe('off')

    await user.click(toggle)
    expect(toggle).toBeChecked()
    expect(localStorage.getItem('sudarshan-order-sound')).toBeNull()
  })

  // SUDARSHAN (M10.7): the market alert sound, its own switch on this device.
  it('turns the market alert sound off and on for this device, apart from the order sound', async () => {
    const user = userEvent.setup()
    render(<DisplaySettings />)
    const market = screen.getByRole('switch', { name: 'Market alert sound' })
    expect(market).toBeChecked()

    await user.click(market)
    expect(market).not.toBeChecked()
    expect(localStorage.getItem('sudarshan-market-sound')).toBe('off')
    expect(screen.getByRole('switch', { name: 'Order alert sound' })).toBeChecked()

    await user.click(market)
    expect(market).toBeChecked()
    expect(localStorage.getItem('sudarshan-market-sound')).toBeNull()
  })

  it('still switches when storage is refused, for the rest of the visit', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    const user = userEvent.setup()
    render(<DisplaySettings />)

    await user.click(screen.getByRole('switch', { name: 'Colour-blind mode' }))

    expect(html.getAttribute('data-cvd')).toBe('1')
  })
})
