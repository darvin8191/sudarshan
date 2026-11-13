/**
 * Resuming (M20.7, amendment R3): no sooner than a minute after it went on, with the phrase,
 * what is open now said first, and a refusal in the server's own words.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

import { PHRASE_HINT } from './phrase'
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

function renderSheet(killSwitch: KillSwitchState, onOpenChange = vi.fn(), clockOffsetMs = 0) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ResumeSheet
        open
        onOpenChange={onOpenChange}
        killSwitch={killSwitch}
        clockOffsetMs={clockOffsetMs}
      />
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
  it('says what to type in the phrase box', () => {
    renderSheet(state(-1))
    expect(screen.getByText(PHRASE_HINT)).toBeInTheDocument()
    expect(PHRASE_HINT).toBe('Type your confirm phrase. It is STOP unless you changed it.')
  })

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

describe("the resume sheet while it sends, and by the server's clock", () => {
  const ok = {
    code: 200,
    ok: true,
    message: 'Kill switch off: new orders go again.',
    state: { known: true, on: false },
    activated: false,
  }

  it('sends once however it is tapped', async () => {
    let answer: (value: typeof ok) => void = () => {}
    api.resume.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    renderSheet(state(-1))
    fireEvent.change(screen.getByLabelText('Confirm phrase'), { target: { value: 'STOP' } })
    const button = resume()
    fireEvent.click(button)
    fireEvent.click(button)
    fireEvent.submit(button.closest('form') as HTMLFormElement)
    await act(async () => answer(ok))
    expect(api.resume).toHaveBeenCalledTimes(1)
  })

  it('cannot be closed while it sends', async () => {
    let answer: (value: typeof ok) => void = () => {}
    api.resume.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    const onOpenChange = renderSheet(state(-1))
    fireEvent.change(screen.getByLabelText('Confirm phrase'), { target: { value: 'STOP' } })
    fireEvent.click(resume())
    await screen.findByText('Resuming')
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(onOpenChange).not.toHaveBeenCalled()
    await act(async () => answer(ok))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })

  it("counts the hold on the server's clock, not the phone's", async () => {
    // The hold ends 90 s from now by the phone's clock, but the server's clock is a minute
    // ahead of the phone's, so by the server's it is 30 s away.
    renderSheet(state(90), vi.fn(), 60_000)
    expect(screen.getByTestId('resume-countdown')).toHaveTextContent(/in (29|30) seconds/)
  })
})
