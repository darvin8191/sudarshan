/**
 * The owner's limits on Settings > Risk (M20.7, M5.14): each card sends only what changed, the
 * server decides whether it loosens a limit and asks for the confirm phrase when it does, and
 * its sentence is shown with an icon. A field that is not a number never turns a limit off.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DailyStatus, RiskSettings, SettingsAnswer } from '@/api/risk'

const saveSettings = vi.hoisted(() => vi.fn())

vi.mock('@/api/risk', async (original) => {
  const actual = await original<typeof import('@/api/risk')>()
  return { ...actual, riskApi: { ...actual.riskApi, saveSettings } }
})

import { PHRASE_FORGOTTEN, PHRASE_HINT } from './phrase'
import {
  BandCard,
  BRAKE_COUNTS,
  DailyLimitEditor,
  locksTodayWords,
  PacingCard,
  PHRASE_FOR_CHANGE,
  PHRASE_FOR_LOOSENING,
  PhraseCard,
  SizeCard,
} from './SettingsCards'

const SETTINGS: RiskSettings = {
  daily_loss_limit: null,
  band_on: false,
  band_opt_pct: 20,
  band_opt_min_rs: 2,
  band_fut_pct: 1,
  band_cash_pct: 5,
  band_both_sides: false,
  size_on: false,
  max_lots_fo: 2,
  max_qty_cash: 1000,
  burst_on: false,
  burst_n: 10,
  burst_window_s: 60,
  revoke_mcp: true,
  phrase_set: true,
  updated_at: '2026-10-28T05:00:00+00:00',
  updated_by: 'owner',
}

function answer(over: Partial<SettingsAnswer> = {}): SettingsAnswer {
  return { code: 200, ok: true, message: 'Saved.', settings: SETTINGS, needsPhrase: false, ...over }
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)
}

beforeEach(() => {
  saveSettings.mockReset()
})

describe('a settings card', () => {
  it('sends only what changed and shows the answer in words', async () => {
    saveSettings.mockResolvedValue(answer())
    wrap(<BandCard settings={SETTINGS} />)
    const save = screen.getByRole('button', { name: 'Save the price band' })
    expect(save).toBeDisabled() // nothing changed yet
    fireEvent.click(screen.getByRole('switch', { name: 'Check prices against the market' }))
    expect(screen.getByTestId('band-card')).toHaveTextContent('Check prices against the marketOn')
    fireEvent.click(save)
    await waitFor(() => expect(saveSettings).toHaveBeenCalledWith({ band_on: true }, undefined))
    const said = await screen.findByText('Saved.')
    expect(said.closest('output')?.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })

  it('asks for the confirm phrase when the server says the change loosens a limit', async () => {
    const loosened = { size_on: true, max_lots_fo: 2, max_qty_cash: 1000 }
    saveSettings
      .mockResolvedValueOnce(
        answer({
          code: 400,
          ok: false,
          needsPhrase: true,
          message: 'Loosening a limit needs your confirm phrase. Nothing was saved.',
        })
      )
      .mockResolvedValueOnce(answer())
    wrap(<SizeCard settings={{ ...SETTINGS, ...loosened }} />)
    fireEvent.change(screen.getByLabelText('F&O order'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the size limit' }))
    expect(await screen.findByText(PHRASE_FOR_LOOSENING)).toBeInTheDocument()
    expect(screen.getByTestId('phrase-hint')).toHaveTextContent(PHRASE_HINT) // what to type
    const confirm = screen.getByRole('button', { name: 'Confirm with your phrase' })
    expect(confirm).toBeDisabled() // until the phrase is typed
    fireEvent.change(screen.getByLabelText('F&O order'), { target: { value: '9' } })
    fireEvent.change(screen.getByLabelText('Confirm phrase'), { target: { value: 'stop' } })
    fireEvent.click(confirm)
    // The change the phrase was asked for, not the fields as they are by then.
    await waitFor(() => expect(saveSettings).toHaveBeenLastCalledWith({ max_lots_fo: 5 }, 'stop'))
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Confirm phrase')).toBeNull()
  })

  it('sends a field that is not a number as typed, for the server to say what it takes', async () => {
    saveSettings.mockResolvedValue(
      answer({ code: 400, ok: false, message: 'The cash band must be between 0.1 and 50%.' })
    )
    wrap(<BandCard settings={SETTINGS} />)
    fireEvent.change(screen.getByLabelText('Cash'), { target: { value: 'five' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the price band' }))
    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith({ band_cash_pct: 'five' }, undefined)
    )
    expect(
      await screen.findByText('The cash band must be between 0.1 and 50%.')
    ).toBeInTheDocument()
  })

  it('sends once however it is tapped', async () => {
    let finish: (value: SettingsAnswer) => void = () => {}
    saveSettings.mockReturnValue(
      new Promise<SettingsAnswer>((resolve) => {
        finish = resolve
      })
    )
    wrap(<BandCard settings={SETTINGS} />)
    fireEvent.click(screen.getByRole('switch', { name: 'Check prices against the market' }))
    const save = screen.getByRole('button', { name: 'Save the price band' })
    fireEvent.click(save)
    fireEvent.click(save)
    fireEvent.click(save)
    finish(answer())
    await screen.findByText('Saved.')
    expect(saveSettings).toHaveBeenCalledTimes(1)
  })
})

describe('the daily loss limit', () => {
  it('turns on at the figure typed, and off by its own button', async () => {
    saveSettings.mockResolvedValue(answer())
    const { unmount } = wrap(<DailyLimitEditor settings={SETTINGS} />)
    const save = screen.getByRole('button', { name: 'Turn the daily limit on' })
    fireEvent.change(screen.getByLabelText('Set a limit for the day'), { target: { value: 'abc' } })
    expect(save).toBeDisabled() // not a number: never sent, so never read as off
    fireEvent.change(screen.getByLabelText('Set a limit for the day'), {
      target: { value: '4,000' },
    })
    fireEvent.click(save)
    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith({ daily_loss_limit: 4000 }, undefined)
    )
    unmount()

    saveSettings.mockResolvedValueOnce(
      answer({
        code: 400,
        ok: false,
        needsPhrase: true,
        message: 'Loosening a limit needs your confirm phrase. Nothing was saved.',
      })
    )
    wrap(<DailyLimitEditor settings={{ ...SETTINGS, daily_loss_limit: 4000 }} />)
    fireEvent.change(screen.getByLabelText('Limit for the day'), { target: { value: '5000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Turn the daily limit off (₹4,000)' }))
    await waitFor(() =>
      expect(saveSettings).toHaveBeenLastCalledWith({ daily_loss_limit: null }, undefined)
    )
    fireEvent.change(await screen.findByLabelText('Confirm phrase'), { target: { value: 'STOP' } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm with your phrase' }))
    // Confirmed as asked: off, never the 5,000 typed meanwhile.
    await waitFor(() =>
      expect(saveSettings).toHaveBeenLastCalledWith({ daily_loss_limit: null }, 'STOP')
    )
  })
})

describe('orders per second and the phrase', () => {
  it("says the server's pace and saves the burst brake", async () => {
    saveSettings.mockResolvedValue(answer())
    wrap(<PacingCard settings={SETTINGS} perSecond={5} />)
    expect(screen.getByTestId('pacing')).toHaveTextContent(
      'Orders go to Upstox at most 5 a second, below the 10 Upstox allows.'
    )
    fireEvent.click(screen.getByRole('switch', { name: 'Pause new orders after a burst' }))
    fireEvent.change(screen.getByLabelText('After'), { target: { value: '8' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the burst brake' }))
    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith({ burst_on: true, burst_n: 8 }, undefined)
    )
  })

  it('warns when the server paces orders at or above what Upstox allows', () => {
    wrap(<PacingCard settings={SETTINGS} perSecond={12} />)
    const pace = screen.getByTestId('pacing')
    expect(pace).toHaveAttribute('role', 'alert')
    expect(pace).toHaveTextContent(
      'Orders go to Upstox at up to 12 a second, and Upstox allows 10: in a burst Upstox refuses the orders past its limit. Set the pace to 9 or less on the server.'
    )
    expect(pace).not.toHaveTextContent('below')
    expect(pace.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })

  it('says what the burst brake counts', () => {
    wrap(<PacingCard settings={SETTINGS} perSecond={9} />)
    expect(screen.getByTestId('pacing')).not.toHaveAttribute('role', 'alert')
    expect(screen.getByTestId('pacing-card')).toHaveTextContent(BRAKE_COUNTS)
    expect(BRAKE_COUNTS).toBe(
      'It counts new orders that add risk; a basket, a split or a multi-leg order counts as one. Changes to an order and exits are never counted or paused.'
    )
  })

  it('changes the phrase and never shows one', async () => {
    saveSettings
      .mockResolvedValueOnce(
        answer({
          code: 400,
          ok: false,
          needsPhrase: true,
          message: 'Changing the confirm phrase needs the phrase it replaces. Nothing was saved.',
        })
      )
      .mockResolvedValueOnce(answer())
    wrap(<PhraseCard />)
    fireEvent.change(screen.getByLabelText('New phrase'), { target: { value: ' Kesari ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change the phrase' }))
    expect(await screen.findByText(PHRASE_FOR_CHANGE)).toBeInTheDocument()
    expect(screen.queryByText(PHRASE_FOR_LOOSENING)).toBeNull() // not "more risk"
    expect(screen.getByTestId('phrase-card')).not.toHaveTextContent('Kesari') // never the new one
    expect(screen.getByTestId('phrase-hint')).toHaveTextContent(PHRASE_HINT) // and what to type
    fireEvent.change(await screen.findByLabelText('Confirm phrase'), { target: { value: 'STOP' } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm with your phrase' }))
    await waitFor(() =>
      expect(saveSettings).toHaveBeenLastCalledWith({ confirm_phrase: 'Kesari' }, 'STOP')
    )
    await waitFor(() => expect(screen.getByLabelText('New phrase')).toHaveValue(''))
  })
})

describe('the phrase card', () => {
  it('says what to type and how a forgotten phrase is set back', () => {
    wrap(<PhraseCard />)
    expect(PHRASE_FORGOTTEN).toBe('If you forget it, it can be set back to STOP on the server.')
    expect(screen.getByTestId('phrase-card')).toHaveTextContent(PHRASE_FORGOTTEN)
  })
})

describe('a change waiting for the phrase', () => {
  it('stays on the card when another card saves meanwhile, so what is confirmed is what is shown', async () => {
    const on = { ...SETTINGS, band_on: true }
    saveSettings
      .mockResolvedValueOnce(
        answer({
          code: 400,
          ok: false,
          needsPhrase: true,
          message: 'Loosening a limit needs your confirm phrase. Nothing was saved.',
        })
      )
      .mockResolvedValueOnce(answer())
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(
      <QueryClientProvider client={client}>
        <BandCard settings={on} />
      </QueryClientProvider>
    )
    fireEvent.click(screen.getByRole('switch', { name: 'Check prices against the market' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save the price band' }))
    expect(await screen.findByLabelText('Confirm phrase')).toBeInTheDocument()
    // Another card saved: a newer version comes in, the band still on.
    view.rerender(
      <QueryClientProvider client={client}>
        <BandCard settings={{ ...on, size_on: true, updated_at: '2026-10-28T05:01:00+00:00' }} />
      </QueryClientProvider>
    )
    expect(screen.getByTestId('band-card')).toHaveTextContent('Check prices against the marketOff')
    fireEvent.change(screen.getByLabelText('Confirm phrase'), { target: { value: 'stop' } })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm with your phrase' }))
    await waitFor(() => expect(saveSettings).toHaveBeenLastCalledWith({ band_on: false }, 'stop'))
  })
})

describe("the daily limit, typed at or below the day's loss", () => {
  function today(over: Partial<DailyStatus>): DailyStatus {
    return {
      figure: -4100,
      approximate: false,
      read_at: '2026-10-28T05:12:07+00:00',
      in_session: true,
      opens_at: null,
      reached: false,
      lock: null,
      unchecked: false,
      ...over,
    }
  }

  it('says saving locks new orders until tomorrow, and saves on a second tap only', async () => {
    saveSettings.mockResolvedValue(answer())
    wrap(
      <DailyLimitEditor
        settings={{ ...SETTINGS, daily_loss_limit: 6000 }}
        days={{ live: today({}), sandbox: today({ figure: -500 }) }}
      />
    )
    fireEvent.change(screen.getByLabelText('Limit for the day'), { target: { value: '4000' } })
    expect(screen.getByTestId('daily-locks-today')).toHaveTextContent(locksTodayWords(4100))
    expect(locksTodayWords(4100)).toBe(
      "Today's loss is already ₹4,100: saving locks new orders until tomorrow."
    )
    fireEvent.click(screen.getByRole('button', { name: 'Save the daily limit' }))
    expect(saveSettings).not.toHaveBeenCalled() // the first tap only asks
    fireEvent.click(
      screen.getByRole('button', { name: 'Save, and lock new orders until tomorrow' })
    )
    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith({ daily_loss_limit: 4000 }, undefined)
    )
  })

  it('asks nothing above the loss, outside the session, or once the day is locked', async () => {
    saveSettings.mockResolvedValue(answer())
    const { unmount } = wrap(
      <DailyLimitEditor
        settings={{ ...SETTINGS, daily_loss_limit: 6000 }}
        days={{
          live: today({ in_session: false }),
          sandbox: today({ lock: { at: '2026-10-28T05:00:00+00:00', figure: -4100, limit: 4000 } }),
        }}
      />
    )
    fireEvent.change(screen.getByLabelText('Limit for the day'), { target: { value: '3000' } })
    expect(screen.queryByTestId('daily-locks-today')).toBeNull()
    unmount()
    wrap(
      <DailyLimitEditor
        settings={{ ...SETTINGS, daily_loss_limit: 6000 }}
        days={{ live: today({}) }}
      />
    )
    fireEvent.change(screen.getByLabelText('Limit for the day'), { target: { value: '5000' } })
    expect(screen.queryByTestId('daily-locks-today')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Save the daily limit' }))
    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith({ daily_loss_limit: 5000 }, undefined)
    )
  })

  it('stops warning once the limit typed is the limit saved', () => {
    saveSettings.mockResolvedValue(answer())
    const days = { live: today({}) }
    const { unmount } = wrap(
      <DailyLimitEditor settings={{ ...SETTINGS, daily_loss_limit: 4000 }} days={days} />
    )
    // Saved at 4000 with 4,100 lost: nothing is being asked, so nothing is warned.
    expect(screen.queryByTestId('daily-locks-today')).toBeNull()
    fireEvent.change(screen.getByLabelText('Limit for the day'), { target: { value: '3500' } })
    expect(screen.getByTestId('daily-locks-today')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Limit for the day'), { target: { value: '4000' } })
    expect(screen.queryByTestId('daily-locks-today')).toBeNull()
    unmount()
  })
})
