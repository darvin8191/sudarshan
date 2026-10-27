/**
 * SUDARSHAN M10.14: the alert settings lead to /profile?tab=theme, so Profile opens on Display,
 * where this device's alert sounds are; /profile on its own still opens on Account.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  webClient: { get: vi.fn(async () => ({ data: { status: 'error' } })), post: vi.fn() },
}))

import ProfilePage from '@/pages/Profile'

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <ProfilePage />
    </MemoryRouter>
  )
}

describe('Profile, opened from the alert settings', () => {
  it('opens on Display, with the market alert sound switch', async () => {
    renderAt('/profile?tab=theme')

    expect(await screen.findByRole('switch', { name: 'Market alert sound' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Display' })).toHaveAttribute('aria-selected', 'true')
  })

  it('opens on Account when the address names no tab', async () => {
    renderAt('/profile')

    expect(await screen.findByRole('tab', { name: 'Account' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.queryByRole('switch', { name: 'Market alert sound' })).toBeNull()
  })
})
