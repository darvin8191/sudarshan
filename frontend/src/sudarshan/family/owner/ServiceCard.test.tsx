import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { AdminState } from '../types'
import { NOW } from './fixtures'
import {
  BOT_MISSING,
  BOT_REFUSED,
  BOT_UNKNOWN,
  CHECK_BAD,
  CHECK_OK,
  CHECK_UNKNOWN,
  ServiceCard,
} from './ServiceCard'

function stateOf(over: Record<string, unknown> = {}): AdminState {
  return {
    enabled: true,
    message: null,
    store_version: 5,
    members: 2,
    sessions: 3,
    sockets: 1,
    ...over,
  } as AdminState
}

describe('the service card', () => {
  it('says On with the store version and the counts', () => {
    render(<ServiceCard state={stateOf()} />)
    expect(screen.getByText('On')).toBeInTheDocument()
    expect(screen.getByText('Store version 5')).toBeInTheDocument()
    expect(screen.getByText('2 members · 3 signed-in devices · 1 open screen')).toBeInTheDocument()
  })

  it('says Off with the service sentence when the family view is switched off', () => {
    render(
      <ServiceCard
        state={stateOf({
          enabled: false,
          message: 'The family view is switched off on the server.',
          store_version: null,
        })}
      />
    )
    expect(screen.getByText('Off')).toBeInTheDocument()
    expect(screen.getByText('The family view is switched off on the server.')).toBeInTheDocument()
    expect(screen.getByText('Store version not read yet')).toBeInTheDocument()
  })

  it('says what the install check found, in a sentence, and says when it is not reported', () => {
    const { rerender } = render(<ServiceCard state={stateOf({ check: { ok: true, at: NOW } })} />)
    expect(screen.getByText(CHECK_OK)).toBeInTheDocument()
    expect(CHECK_OK).toBe(
      "Checked at install: the family service cannot read the account's secrets"
    )
    rerender(<ServiceCard state={stateOf({ check: { ok: false, at: NOW } })} />)
    expect(screen.getByText(CHECK_BAD)).toBeInTheDocument()
    expect(screen.queryByText(CHECK_OK)).toBeNull()
    rerender(<ServiceCard state={stateOf()} />)
    expect(screen.getByText(CHECK_UNKNOWN)).toBeInTheDocument()
  })
})

describe('the Family bot card', () => {
  it('says Polling with the time of the last message, in India time', () => {
    render(
      <ServiceCard
        state={stateOf({
          bot: { state: 'polling', last_message_at: Date.UTC(2026, 10, 14, 10, 35) },
        })}
      />
    )
    expect(screen.getByText('Polling · last message 16:05')).toBeInTheDocument()
  })

  it('says Polling with no message yet', () => {
    render(<ServiceCard state={stateOf({ bot: { state: 'polling', last_message_at: null } })} />)
    expect(screen.getByText('Polling · no message yet')).toBeInTheDocument()
  })

  it('says the token is missing, and that Telegram refused it', () => {
    const { rerender } = render(
      <ServiceCard state={stateOf({ bot: { state: 'token_missing' } })} />
    )
    expect(screen.getByText(BOT_MISSING)).toBeInTheDocument()
    expect(BOT_MISSING).toBe('Token missing: see sudarshan/family/README.md')
    rerender(<ServiceCard state={stateOf({ bot: { state: 'token_refused' } })} />)
    expect(screen.getByText(BOT_REFUSED)).toBeInTheDocument()
    expect(BOT_REFUSED).toBe('Telegram refused the token')
  })

  it('says it is not reported when the service says nothing, or something it does not know', () => {
    const { rerender } = render(<ServiceCard state={stateOf()} />)
    expect(screen.getByText(BOT_UNKNOWN)).toBeInTheDocument()
    rerender(<ServiceCard state={stateOf({ bot: { state: 'exploded' } })} />)
    expect(screen.getByText(BOT_UNKNOWN)).toBeInTheDocument()
  })
})
