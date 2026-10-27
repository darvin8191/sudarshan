/**
 * Resuming (M20.7, amendment R3): no sooner than a minute after it went on, with the phrase,
 * what is open now said first, and a refusal in the server's own words.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { KillSwitchState } from '@/api/risk'

const api = vi.hoisted(() => ({ preview: vi.fn(), resume: vi.fn() }))
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}))
vi.mock('@/api/risk', () => ({ riskApi: api }))
vi.mock('@/utils/toast', () => ({ showToast: toast }))

import { openNow, ResumeSheet, secondsLeft } from './ResumeSheet'

function state(resumeInS: number): KillSwitchState {
  return {
    known: true,
    on: true,
    since: new Date(Date.now() - 30_000).toISOString(),
    surface: 'telegram',
    running: false,
    can_resume_at: new Date(Date.now() + resumeInS * 1000).toISOString(),
  }
}

function renderSheet(killSwitch: KillSwitchState, onOpenChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ResumeSheet open onOpenChange={onOpenChange} killSwitch={killSwitch} />
    </QueryClientProvider>
  )
  return onOpenChange
}

const resume = () => screen.getByRole('button', { name: 'Resume trading' })

beforeEach(() => {
  vi.clearAllMocks()
  api.preview.mockResolvedValue({
    counts: { orders: 1, gtts: 0, stops: 0, protected: 0, positions: 2 },
    message: '',
  })
})

describe('resuming', () => {
  it('waits out the minute, whatever is typed', async () => {
    renderSheet(state(30))
    fireEvent.change(screen.getByLabelText('Confirm phrase'), { target: { value: 'STOP' } })
    expect(screen.getByTestId('resume-countdown')).toHaveTextContent(
      /you can resume in (29|30) seconds/
    )
    expect(resume()).toBeDisabled()
  })

  it('says what is open now, then resumes with the phrase', async () => {
    api.resume.mockResolvedValue({
      code: 200,
      ok: true,
      message: 'Kill switch off: new orders go again.',
      state: { known: true, on: false },
      activated: false,
    })
    const onOpenChange = renderSheet(state(-1))
    expect(
      await screen.findByText('Open now: 2 open positions, 1 open order, 0 GTTs.')
    ).toBeVisible()
    expect(screen.getByTestId('resume-countdown')).toHaveTextContent('You can resume now.')
    expect(resume()).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Confirm phrase'), { target: { value: 'stop' } })
    fireEvent.click(resume())
    await waitFor(() => expect(api.resume).toHaveBeenCalledWith('stop'))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    expect(toast.success).toHaveBeenCalledWith('Kill switch off: new orders go again.')
  })

  it("says a refusal in the server's words and keeps the sheet open", async () => {
    api.resume.mockResolvedValue({
      code: 409,
      ok: false,
      message: 'Exit all is still being placed. Wait for it, then resume.',
      state: null,
      activated: false,
    })
    const onOpenChange = renderSheet(state(-1))
    fireEvent.change(screen.getByLabelText('Confirm phrase'), { target: { value: 'STOP' } })
    fireEvent.click(resume())
    expect(await screen.findByRole('alert')).toHaveTextContent('Exit all is still being placed')
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
  })

  it('counts and words', () => {
    expect(secondsLeft(null, Date.now())).toBe(0)
    expect(secondsLeft(new Date(Date.now() + 1500).toISOString(), Date.now())).toBe(2)
    expect(openNow(null)).toBe('Open positions and orders could not be read just now.')
    expect(openNow({ orders: 1, gtts: null, stops: 0, protected: 0, positions: 1 })).toBe(
      'Open now: 1 open position, 1 open order.'
    )
  })
})
