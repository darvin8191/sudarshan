import { describe, expect, it } from 'vitest'
import { DEFAULT_CHAIN, readLastChain, saveLastChain } from './prefs'
import { daysLeft, expiryLabel, foExchange, oiWords, optionsPath, strikeWords } from './words'

const AT = Date.UTC(2026, 10, 16, 5, 12, 5) // 16 Nov 2026, 10:42 in India

describe('the options screen’s words', () => {
  it('counts days to an expiry by India’s calendar', () => {
    expect(daysLeft('2026-11-17', AT)).toBe(1)
    expect(daysLeft('2026-11-16', AT)).toBe(0)
    // 19:00 UTC on the 16th is already the 17th in India.
    expect(daysLeft('2026-11-17', Date.UTC(2026, 10, 16, 19, 0, 0))).toBe(0)
    expect(expiryLabel('2026-12-29', AT)).toBe('29 Dec · 43d')
    expect(expiryLabel('2026-11-16', AT)).toBe('16 Nov · today')
  })

  it('says open interest in lakh and crore', () => {
    expect(oiWords(4_210_000)).toBe('42.1 L')
    expect(oiWords(11_200_000)).toBe('1.12 Cr')
    expect(oiWords(65_000)).toBe('65,000')
    expect(oiWords(-320_000)).toBe('3.2 L')
    expect(oiWords(0)).toBe('0')
  })

  it('writes a strike as it is listed', () => {
    expect(strikeWords(25_900)).toBe('25,900')
    expect(strikeWords(84_500)).toBe('84,500')
    expect(strikeWords(102.5)).toBe('102.5')
    expect(strikeWords(0.25)).toBe('0.25')
  })

  it('reads the F&O exchange from either side of the address', () => {
    expect(['nfo', 'NSE', 'NSE_INDEX'].map(foExchange)).toEqual(['NFO', 'NFO', 'NFO'])
    expect(['BFO', 'bse_index'].map(foExchange)).toEqual(['BFO', 'BFO'])
    expect(foExchange('MCX')).toBe('MCX')
    expect(foExchange('CDS')).toBeNull()
    expect(foExchange(undefined)).toBeNull()
  })

  it('builds the address other screens link to', () => {
    expect(optionsPath('NFO', 'NIFTY')).toBe('/options/NFO/NIFTY')
    expect(optionsPath('NFO', 'M&M', { expiry: '2026-11-24', strike: 25_900 })).toBe(
      '/options/NFO/M%26M?expiry=2026-11-24&strike=25900'
    )
  })
})

describe('what the options screen remembers', () => {
  const box = () => {
    const held = new Map<string, string>()
    return {
      getItem: (key: string) => held.get(key) ?? null,
      setItem: (key: string, value: string) => void held.set(key, value),
    }
  }

  it('gives NIFTY’s nearest expiry until something is saved, then what was saved', () => {
    const storage = box()
    expect(readLastChain(storage)).toEqual(DEFAULT_CHAIN)
    saveLastChain({ exchange: 'BFO', underlying: 'SENSEX', expiry: '2026-11-19' }, storage)
    expect(readLastChain(storage)).toEqual({
      exchange: 'BFO',
      underlying: 'SENSEX',
      expiry: '2026-11-19',
    })
  })

  it.each([
    ['not json', '{'],
    ['another exchange', '{"exchange":"MCX","underlying":"GOLD"}'],
    ['a name that is not a symbol', '{"exchange":"NFO","underlying":"../x"}'],
    ['a null', 'null'],
  ])('reads the default from %s', (_what, saved) => {
    const storage = box()
    storage.setItem('sudarshan-options-last', saved)
    expect(readLastChain(storage)).toEqual(DEFAULT_CHAIN)
  })

  it('drops an expiry that is not a date and survives blocked storage', () => {
    const storage = box()
    storage.setItem(
      'sudarshan-options-last',
      '{"exchange":"NFO","underlying":"NIFTY","expiry":"soon"}'
    )
    expect(readLastChain(storage).expiry).toBeNull()
    const blocked = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    }
    expect(readLastChain(blocked)).toEqual(DEFAULT_CHAIN)
    expect(() => saveLastChain(DEFAULT_CHAIN, blocked)).not.toThrow()
  })
})
