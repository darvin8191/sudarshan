import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { cardOf, NOW } from './fixtures'
import { MemberCard } from './MemberCard'

describe('a member card', () => {
  it('shows the member in words: role, status, devices, privacy, Telegram, last seen', () => {
    render(<MemberCard card={cardOf()} now={NOW} onOpen={() => {}} />)
    expect(screen.getByText('Test One')).toBeInTheDocument()
    expect(screen.getByText('Sign-in name test_one')).toBeInTheDocument()
    expect(screen.getByText('Viewer')).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByText('2 devices')).toBeInTheDocument()
    expect(
      screen.getByText('P&L shown · quantities shown · funds hidden · orders shown')
    ).toBeInTheDocument()
    expect(screen.getByText('Telegram linked')).toBeInTheDocument()
    expect(screen.getByText('Last seen today 14:30')).toBeInTheDocument()
  })

  it('says Child for a child, Telegram not linked, and not signed in before the first sign-in', () => {
    render(
      <MemberCard
        card={cardOf({
          role: 'child',
          handle: null,
          telegram_linked: false,
          last_seen_at: null,
          devices: 0,
        })}
        now={NOW}
        onOpen={() => {}}
      />
    )
    expect(screen.getByText('Child')).toBeInTheDocument()
    expect(screen.getByText('Not signed in yet')).toBeInTheDocument()
    expect(screen.getByText('Telegram not linked')).toBeInTheDocument()
    expect(screen.getByText('Not seen yet')).toBeInTheDocument()
    expect(screen.getByText('No devices')).toBeInTheDocument()
  })

  it('shows each status as a word, never as a colour alone', () => {
    const cases = [
      [
        cardOf({ status: 'invited', handle: null, invite_expires_at: NOW + 600_000 }),
        'Invited · expires 15:40',
      ],
      [cardOf({ locked_until: NOW + 300_000 }), 'Locked until 15:35'],
      [cardOf({ status: 'suspended' }), 'Suspended'],
    ] as const
    for (const [card, words] of cases) {
      const { unmount } = render(<MemberCard card={card} now={NOW} onOpen={() => {}} />)
      expect(screen.getByText(words)).toBeInTheDocument()
      unmount()
    }
  })

  it('opens the member by id when tapped, and is a button at least 48 dp tall', async () => {
    const onOpen = vi.fn()
    render(<MemberCard card={cardOf({ id: 4 })} now={NOW} onOpen={onOpen} />)
    const button = screen.getByRole('button')
    expect(button.className).toContain('min-h-12')
    await userEvent.click(button)
    expect(onOpen).toHaveBeenCalledWith(4)
  })
})
