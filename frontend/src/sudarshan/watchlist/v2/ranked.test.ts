/**
 * F16: the ranked list's words and refs, with vectors typed by hand from the design (3.6) and the
 * server's own sentences (`words/ranked.py`), so a change to either shows here.
 */
import { describe, expect, it } from 'vitest'
import {
  ageLine,
  ageWords,
  isStale,
  leftOutSentence,
  parseRankedRef,
  rankedLabel,
  readAgo,
  shortName,
  timesLine,
  underlyingChip,
  updatedLine,
} from './ranked'

const T = Date.UTC(2026, 10, 13, 5, 13, 10) // 10:43:10 IST
const ago = (seconds: number) => T - seconds * 1000

describe('the age', () => {
  it('counts "just now", minutes and hours from made_at', () => {
    expect(ageWords(ago(0), T)).toBe('just now')
    expect(ageWords(ago(59), T)).toBe('just now')
    expect(ageWords(ago(120), T)).toBe('2 min ago')
    expect(ageWords(ago(6 * 60 + 30), T)).toBe('6 min ago')
    expect(ageWords(ago(2 * 3600 + 60), T)).toBe('2 h ago')
    expect(ageWords(ago(2 * 86_400), T)).toBe('2 d ago')
  })

  it('is stale after ten minutes, and the line then says how to read it again', () => {
    expect(isStale(ago(600), T)).toBe(false)
    expect(isStale(ago(601), T)).toBe(true)
    expect(ageLine(ago(120), T)).toEqual({ text: 'Ranked by Upstox · 2 min ago', stale: false })
    expect(ageLine(ago(11 * 60), T)).toEqual({
      text: 'Ranked by Upstox · 11 min ago · Pull down to read it again',
      stale: true,
    })
  })

  it('never says Live', () => {
    for (const seconds of [0, 120, 700, 9000]) {
      expect(ageLine(ago(seconds), T).text).not.toMatch(/live/i)
    }
  })

  it('shows both times on a tap, in Indian time', () => {
    expect(timesLine(Date.UTC(2026, 10, 13, 5, 12, 5), T)).toBe(
      'Ranking made 10:42:05, read 10:43:10'
    )
  })

  it('counts the Refresh ranking button’s minute in seconds', () => {
    expect(readAgo(ago(20), T)).toBe('Read 20 s ago')
  })
})

describe('the sentences', () => {
  const two = "2 contracts from Upstox are not in today's contract list and are left out."

  it('shows the server’s left-out sentence as sent, whatever its wording', () => {
    expect(leftOutSentence({ left_out: 2, left_out_words: two })).toBe(two)
    const reworded = 'Two of Upstox’s contracts are missing from today’s list.'
    expect(leftOutSentence({ left_out: 2, left_out_words: reworded })).toBe(reworded)
  })

  it('is nothing when the server says nothing was left out, and never reads `words`', () => {
    expect(leftOutSentence({ left_out: 0, left_out_words: null })).toBeNull()
    const answer = {
      left_out: 0,
      left_out_words: null,
      words: `Ranked by Upstox · 2 min ago ${two}`,
    }
    expect(leftOutSentence(answer)).toBeNull()
  })

  it('writes the sentence from the count for an answer that predates the field', () => {
    expect(leftOutSentence({ left_out: 2 })).toBe(two)
    expect(leftOutSentence({ left_out: 1 })).toBe(
      "1 contract from Upstox is not in today's contract list and is left out."
    )
    expect(leftOutSentence({ left_out: 0 })).toBeNull()
  })

  it('says what is new after a read that changed the order', () => {
    expect(updatedLine(T, ['NIFTY 25,950 CE', 'NIFTY 26,000 CE', 'NIFTY 26,050 PE'])).toBe(
      'Ranking updated 10:43 · 3 new: NIFTY 25,950 CE, NIFTY 26,000 CE, NIFTY 26,050 PE'
    )
    expect(updatedLine(T, ['A', 'B', 'C', 'D', 'E'])).toBe(
      'Ranking updated 10:43 · 5 new: A, B, C and 2 more'
    )
    expect(updatedLine(T, [])).toBe('Ranking updated 10:43 · the order changed')
  })

  it('counts an underlying’s contracts', () => {
    expect(underlyingChip(12, 50, 'NIFTY')).toBe('12 of the top 50 are NIFTY contracts')
  })

  it('names a contract as a trader says it', () => {
    expect(shortName('NIFTY25NOV2625950CE', 'NFO')).toBe('NIFTY 25,950 CE')
    expect(shortName('RELIANCE25NOV26FUT', 'NFO')).toBe('RELIANCE FUT')
  })
})

describe('the refs', () => {
  it('parses what the server offers and nothing else', () => {
    expect(parseRankedRef('ranked:futures:INDEX:OI_GAINERS')).toEqual({
      src: 'futures',
      asset: 'INDEX',
      cat: 'OI_GAINERS',
    })
    expect(parseRankedRef('ranked:futures:INDEX')).toBeNull()
    expect(parseRankedRef('ranked:mtf:INDEX:OI_GAINERS')).toBeNull()
    expect(parseRankedRef('preset:indices')).toBeNull()
    expect(parseRankedRef(null)).toBeNull()
  })

  it('labels a ranking as the server does', () => {
    expect(rankedLabel('ranked:futures:INDEX:OI_GAINERS')).toBe('Index futures · OI gainers')
    expect(rankedLabel('ranked:options:STOCK:UNDER_5000')).toBe(
      'Stock options · Under ₹5,000 to buy'
    )
  })
})
