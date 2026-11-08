import { describe, expect, it } from 'vitest'
import { INDICATORS, OPERATORS } from './catalog'
import type { Clause } from './types'
import { clauseSentence, offsetWords, operandChips, operatorChip, tfWords } from './words'

describe('a clause in the owner words', () => {
  const cross: Clause = {
    id: 'c1',
    on: true,
    left: { ind: 'ema', p: { length: 20 }, tf: 15 },
    op: 'crosses_above',
    right: { ind: 'ema', p: { length: 50 }, tf: 15 },
  }

  it('writes an indicator with its parameters and the timeframe as its own chip', () => {
    expect(operandChips(cross.left)).toEqual({ tf: '15 min', text: 'EMA 20', at: null })
    expect(
      operandChips({ ind: 'macd', p: { fast: 12, slow: 26, signal: 9 }, out: 'signal', tf: 5 }).text
    ).toBe('MACD 12, 26, 9 signal')
    expect(operandChips({ ind: 'rsi', tf: 'D' })).toEqual({ tf: 'Day', text: 'RSI 14', at: null })
  })

  it('writes a field, an offset, a typed number and a calculation', () => {
    expect(operandChips({ field: 'close', tf: 15, at: { ago: 1 } })).toEqual({
      tf: '15 min',
      text: 'Close',
      at: '1 candle ago',
    })
    expect(operandChips({ field: 'prev_day_high' })).toEqual({
      tf: null,
      text: "Yesterday's high",
      at: null,
    })
    expect(operandChips({ num: '55' })).toEqual({ tf: null, text: '55', at: null })
    expect(
      operandChips({
        math: 'mul',
        args: [{ num: '1.5' }, { ind: 'sma', p: { length: 20, source: 'volume' }, tf: 15 }],
      }).text
    ).toBe('1.5 × 15 min SMA 20 of volume')
  })

  it('names every offset the way the builder offers it', () => {
    expect(offsetWords(0)).toBe('Latest candle')
    expect(offsetWords({ ago: 3 })).toBe('3 candles ago')
    expect(offsetWords('first_today')).toBe("Today's first candle")
    expect(offsetWords('last_prev_day')).toBe("Yesterday's last candle")
    expect([tfWords(1), tfWords(60), tfWords('D'), tfWords('W')]).toEqual([
      '1 min',
      '60 min',
      'Day',
      'Week',
    ])
  })

  it('writes the operator with its arguments in', () => {
    expect(operatorChip({ op: 'crosses_above' })).toBe('Crosses above')
    expect(operatorChip({ op: 'within_pct', args: { pct: '0.5' } })).toBe('Is within 0.5 % of')
    expect(operatorChip({ op: 'rising', args: { n: 3 } })).toBe('Is rising for 3 candles')
    expect(operatorChip({ op: 'between', args: { low: '30', high: '70' } })).toBe(
      'Is between 30 and 70'
    )
    expect(operatorChip({ op: 'is', args: { name: 'bullish_engulfing' } })).toBe(
      'Is Bullish engulfing'
    )
    expect(operatorChip({ op: 'is', args: { name: 'long_buildup' } })).toBe('Is Long build-up')
  })

  it('reads a whole clause as one sentence for a screen reader', () => {
    expect(clauseSentence(cross)).toBe('15 min EMA 20 crosses above 15 min EMA 50')
  })

  it('keeps "is not" out of the operators: it lives in the list as "is at or below"', () => {
    expect(OPERATORS.map((operator) => operator.label)).toContain('Is at or below')
    expect(OPERATORS.some((operator) => /\bnot\b/i.test(operator.label))).toBe(false)
  })

  it('offers only indicators the catalogue knows, each with an output', () => {
    for (const indicator of INDICATORS) expect(indicator.outs.length).toBeGreaterThan(0)
  })
})
