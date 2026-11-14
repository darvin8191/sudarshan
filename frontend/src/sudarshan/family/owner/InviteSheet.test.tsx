import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cardOf, inviteOf, NOW } from './fixtures'
import { InviteSheet, linkOf } from './InviteSheet'

afterEach(() => {
  vi.unstubAllGlobals()
})

function mount(over: Partial<React.ComponentProps<typeof InviteSheet>> = {}) {
  const props = {
    invite: inviteOf(),
    name: cardOf().display_name,
    now: NOW,
    onClose: vi.fn(),
    ...over,
  }
  const view = render(<InviteSheet {...props} />)
  return { props, ...view }
}

describe('the invite link', () => {
  it('is this site and the invite path, whatever form the path comes in', () => {
    expect(linkOf(inviteOf(), 'https://example.test')).toBe(
      'https://example.test/family/join/test-code-0001'
    )
    expect(linkOf(inviteOf({ path: 'join/x' }), 'https://example.test')).toBe(
      'https://example.test/family/join/test-code-0001'
    )
  })

  it('is shown with its expiry, the one-use words and who sends it', () => {
    mount()
    expect(screen.getByText('Invite link for Test One')).toBeInTheDocument()
    expect(screen.getByText('Expires at 17:30 · one use')).toBeInTheDocument()
    expect(screen.getByTestId('invite-link').textContent).toContain('/family/join/test-code-0001')
    expect(
      screen.getByText('Send it yourself by WhatsApp or Telegram. SUDARSHAN never sends it.')
    ).toBeInTheDocument()
    expect(screen.getByText(/shown once/)).toBeInTheDocument()
  })

  it('is copied with Copy link', async () => {
    const writeText = vi.fn(async () => {})
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Copy link' }))
    expect(writeText).toHaveBeenCalledWith(expect.stringMatching(/\/family\/join\/test-code-0001$/))
    expect(await screen.findByText('Link copied.')).toBeInTheDocument()
  })

  it('says how to copy by hand when the clipboard refuses', async () => {
    vi.stubGlobal('navigator', {
      ...navigator,
      clipboard: {
        writeText: async () => {
          throw new Error('denied')
        },
      },
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Copy link' }))
    expect(await screen.findByText(/could not be copied/)).toBeInTheDocument()
  })

  it('opens the share sheet where the phone has one, and offers no Share where it has not', async () => {
    const share = vi.fn(async () => {})
    vi.stubGlobal('navigator', { ...navigator, share })
    const { unmount } = mount()
    await userEvent.click(screen.getByRole('button', { name: 'Share' }))
    expect(share).toHaveBeenCalledWith(
      expect.objectContaining({ url: expect.stringMatching(/\/family\/join\/test-code-0001$/) })
    )
    unmount()
    vi.stubGlobal('navigator', { clipboard: navigator.clipboard })
    mount()
    expect(screen.queryByRole('button', { name: 'Share' })).toBeNull()
  })

  it('ends at its expiry, as the clock moves, and the buttons stop', () => {
    const { rerender, props } = mount()
    expect(screen.getByRole('button', { name: 'Copy link' })).toBeEnabled()
    act(() => {
      rerender(<InviteSheet {...props} now={NOW + 7_200_000} />)
    })
    expect(screen.getByText('Expired at 17:30')).toBeInTheDocument()
    expect(
      screen.getByText('This link has expired. Close this and make a new invite.')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy link' })).toBeDisabled()
  })

  it('is kept nowhere: not in storage, not in the address bar', () => {
    const before = window.location.href
    mount()
    expect(window.location.href).toBe(before)
    for (const store of [localStorage, sessionStorage]) {
      for (let i = 0; i < store.length; i++) {
        expect(store.getItem(store.key(i) as string)).not.toContain('test-code-0001')
      }
    }
  })

  it('is gone when the page lets go of it', () => {
    const { rerender, props } = mount()
    rerender(<InviteSheet {...props} invite={null} />)
    expect(screen.queryByTestId('invite-link')).toBeNull()
    expect(document.body.textContent).not.toContain('test-code-0001')
  })

  it('tells the page when it is closed', async () => {
    const { props } = mount()
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(props.onClose).toHaveBeenCalled()
  })
})
