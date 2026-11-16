/**
 * M13.7 and M20.5 (U4): "Why", TRINETRA's own explanation in the language the owner picked, or the
 * first one it sent with a line saying so, beside the inputs it published; and the language picker,
 * one tap saving the owner's setting once. Readings, settings and refusals are the real bridge's
 * (bridge.answers.json: Hinglish to start with, Gujarati saved, and a save another screen beat).
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const webClient = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient }))

import { ANSWERS, answerFromRecording, NOW_MS, type Recorded, stateOf } from '@/test/signalsAnswers'
import { renderSignals } from '@/test/signalsRender'
import type { Change, Reading } from './types'
import { WORDS } from './types'
import { changeSubject, readingSubject, WhySheet } from './WhySheet'

function readingOf(module: string, instrument: string): Reading {
  const found = stateOf().readings.find(
    (each) => each.module === module && each.instrument === instrument
  )
  if (!found) throw new Error('not in the recording')
  return found
}

/** The bridge: the settings as read, and the answer the save gets. */
function bridge(saved: Recorded = ANSWERS.settings_saved) {
  answerFromRecording(webClient, (method) => (method === 'GET' ? ANSWERS.settings : saved))
}

beforeEach(() => {
  webClient.get.mockReset()
  webClient.request.mockReset()
})

describe('Why', () => {
  it("in the owner's Hinglish, with the inputs TRINETRA chose to publish", async () => {
    bridge()
    const subject = readingSubject(readingOf('regime', 'NIFTY'), NOW_MS, true)
    renderSignals(<WhySheet subject={subject} stateLang="hi" onClose={() => {}} />)
    const sheet = await screen.findByRole('dialog', { name: 'DRISHTI · NIFTY' })
    expect(sheet).toHaveTextContent('Reading of 09:04')
    expect(sheet).toHaveTextContent('FII distributionbearishconfidence 0.71')
    await waitFor(() =>
      expect(within(sheet).getByRole('radio', { name: 'Hinglish' })).toBeChecked()
    )
    const text = within(sheet).getByTestId('why-text')
    expect(text).toHaveTextContent('Invented: teen sessions se index futures mein net short')
    expect(text).toHaveAttribute('lang', 'hi-Latn')
    const inputs = within(sheet).getByRole('region', { name: 'Inputs' })
    expect(inputs).toHaveTextContent('FII index futures, net-1.2 lakh contracts')
    expect(inputs).toHaveTextContent('Put-call ratio0.82')
  })

  it('a tap on Gujarati saves it once, with the version read, and the text follows', async () => {
    bridge()
    const subject = readingSubject(readingOf('regime', 'NIFTY'), NOW_MS, true)
    renderSignals(<WhySheet subject={subject} stateLang="hi" onClose={() => {}} />)
    const sheet = await screen.findByRole('dialog')
    const gujarati = within(sheet).getByRole('radio', { name: 'Gujarati' })
    await waitFor(() => expect(gujarati).toBeEnabled())
    await userEvent.click(gujarati)
    await waitFor(() => expect(gujarati).toBeChecked())
    expect(webClient.request).toHaveBeenCalledTimes(1)
    expect(webClient.request.mock.calls[0][0]).toMatchObject({
      method: 'PUT',
      data: { version: 1, lang: 'gu' },
    })
    expect(within(sheet).getByTestId('why-text')).toHaveAttribute('lang', 'gu')
  })

  it('a save another screen made first: said, nothing saved, the settings read again', async () => {
    bridge(ANSWERS.settings_conflict)
    const subject = readingSubject(readingOf('regime', 'NIFTY'), NOW_MS, true)
    renderSignals(<WhySheet subject={subject} stateLang="hi" onClose={() => {}} />)
    const sheet = await screen.findByRole('dialog')
    const english = within(sheet).getByRole('radio', { name: 'English' })
    await waitFor(() => expect(english).toBeEnabled())
    await userEvent.click(english)
    expect(await within(sheet).findByRole('alert')).toHaveTextContent(WORDS.version_conflict)
    expect(within(sheet).getByRole('radio', { name: 'Hinglish' })).toBeChecked()
    await waitFor(() => expect(webClient.get.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('not written in the chosen language: the first TRINETRA sent, said as such', async () => {
    bridge()
    const subject = readingSubject(readingOf('regime', 'BANKNIFTY'), NOW_MS, true)
    renderSignals(<WhySheet subject={subject} stateLang="gu" onClose={() => {}} />)
    const sheet = await screen.findByRole('dialog', { name: 'DRISHTI · BANKNIFTY' })
    // The settings say Hinglish once read; BANKNIFTY's explanation is in English only.
    await waitFor(() =>
      expect(sheet).toHaveTextContent('TRINETRA did not write this in Hinglish; shown in English.')
    )
    expect(within(sheet).getByTestId('why-text')).toHaveTextContent(
      'Invented: no side has held the index for a week.'
    )
    expect(within(sheet).getByRole('region', { name: 'Inputs' })).toHaveTextContent(
      'TRINETRA published no inputs with this reading.'
    )
  })

  it("no explanation at all; and TRINETRA's text stays text, never markup", async () => {
    bridge()
    const quiet = readingSubject(readingOf('gap_bias', 'BANKNIFTY'), NOW_MS, true)
    const view = renderSignals(<WhySheet subject={quiet} stateLang="hi" onClose={() => {}} />)
    expect(await screen.findByText(WORDS.why_none)).toBeInTheDocument()
    view.unmount()
    const change = structuredClone(
      (ANSWERS.timeline.answer.data as { changes: Change[] }).changes[4]
    )
    change.why = { en: '<b>bold</b> <img src=x onerror=alert(1)>', hi: null, gu: null }
    renderSignals(
      <WhySheet subject={changeSubject(change, NOW_MS)} stateLang="en" onClose={() => {}} />
    )
    const sheet = await screen.findByRole('dialog')
    expect(sheet).toHaveTextContent('Changed 09:04')
    expect(within(sheet).getByTestId('why-text').textContent).toBe(
      '<b>bold</b> <img src=x onerror=alert(1)>'
    )
    expect(sheet.querySelector('b, img')).toBeNull()
  })
})
