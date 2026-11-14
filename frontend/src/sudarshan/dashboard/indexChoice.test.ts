import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  DEFAULT_INDICES,
  INDEX_CHOICES,
  INDEX_REFUSED,
  MAX_INDICES,
  resetDashboardIndicesForTests,
  setDashboardIndex,
  useDashboardIndices,
} from './indexChoice'

const KEY = 'sudarshan-dashboard-indices'
const now = () => renderHook(() => useDashboardIndices()).result.current

beforeEach(() => {
  localStorage.clear()
  resetDashboardIndicesForTests()
})

describe("the owner's choice of dashboard indices", () => {
  it('is NIFTY 50, NIFTY BANK, INDIA VIX and SENSEX until he chooses', () => {
    expect(now()).toEqual(['NIFTY', 'BANKNIFTY', 'INDIAVIX', 'SENSEX'])
    expect(DEFAULT_INDICES).toHaveLength(4)
    expect(MAX_INDICES).toBe(6)
    for (const symbol of DEFAULT_INDICES)
      expect(INDEX_CHOICES.some((choice) => choice.symbol === symbol)).toBe(true)
  })

  it('adds one and keeps it on this device, in the order the indices are offered', () => {
    expect(setDashboardIndex('FINNIFTY', true)).toBeNull()
    expect(now()).toEqual(['NIFTY', 'BANKNIFTY', 'INDIAVIX', 'SENSEX', 'FINNIFTY'])
    expect(JSON.parse(localStorage.getItem(KEY) ?? '[]')).toEqual(now())
    setDashboardIndex('INDIAVIX', false)
    setDashboardIndex('INDIAVIX', true)
    expect(now()).toEqual(['NIFTY', 'BANKNIFTY', 'INDIAVIX', 'SENSEX', 'FINNIFTY'])
  })

  it('refuses the seventh with a sentence, and changes nothing', () => {
    setDashboardIndex('FINNIFTY', true)
    setDashboardIndex('MIDCPNIFTY', true)
    expect(now()).toHaveLength(6)
    expect(setDashboardIndex('BANKEX', true)).toBe('full')
    expect(INDEX_REFUSED.full).toBe('Six indices at most. Turn one off to show another.')
    expect(now()).toHaveLength(6)
    expect(now()).not.toContain('BANKEX')
  })

  it('refuses to turn off the last one', () => {
    for (const symbol of ['BANKNIFTY', 'INDIAVIX', 'SENSEX']) setDashboardIndex(symbol, false)
    expect(setDashboardIndex('NIFTY', false)).toBe('last')
    expect(now()).toEqual(['NIFTY'])
  })

  it('reads what is stored defensively: unknown symbols dropped, too many cut, nonsense ignored', () => {
    localStorage.setItem(KEY, JSON.stringify(['SENSEX', 'DOGE', 'NIFTY']))
    resetDashboardIndicesForTests()
    expect(now()).toEqual(['NIFTY', 'SENSEX'])
    localStorage.setItem(KEY, JSON.stringify(INDEX_CHOICES.map((choice) => choice.symbol)))
    resetDashboardIndicesForTests()
    expect(now()).toHaveLength(6)
    for (const bad of ['{', '"NIFTY"', '[]', '["DOGE"]']) {
      localStorage.setItem(KEY, bad)
      resetDashboardIndicesForTests()
      expect(now(), bad).toEqual(DEFAULT_INDICES)
    }
  })

  it('follows another tab that changed it', () => {
    localStorage.setItem(KEY, JSON.stringify(['NIFTY']))
    window.dispatchEvent(new StorageEvent('storage', { key: KEY }))
    expect(now()).toEqual(['NIFTY'])
  })
})
