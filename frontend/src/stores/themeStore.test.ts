/**
 * SUDARSHAN is light only. The store keeps `mode` for the upstream readers that
 * branch on it, so the contract is what this file pins: it never leaves
 * 'light', and nothing it does puts `.dark` on <html>.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useThemeStore } from './themeStore'

const html = document.documentElement
const STORAGE_KEY = 'openalgo-theme'

function reset() {
  localStorage.clear()
  html.className = ''
  useThemeStore.setState({ mode: 'light', color: 'zinc', appMode: 'live', isTogglingMode: false })
}

describe('light-only theme store', () => {
  beforeEach(reset)
  afterEach(reset)

  it('refuses dark', () => {
    useThemeStore.getState().setMode('dark')
    expect(useThemeStore.getState().mode).toBe('light')
    expect(html.classList.contains('dark')).toBe(false)
  })

  it('has nothing to toggle to', () => {
    useThemeStore.getState().toggleMode()
    useThemeStore.getState().toggleMode()
    expect(useThemeStore.getState().mode).toBe('light')
    expect(html.classList.contains('dark')).toBe(false)
  })

  it('switches Analyze mode with its own class and never with dark', () => {
    useThemeStore.getState().setAppMode('analyzer')
    expect(html.classList.contains('analyzer')).toBe(true)
    expect(html.classList.contains('dark')).toBe(false)

    useThemeStore.getState().setAppMode('live')
    expect(html.classList.contains('analyzer')).toBe(false)
    expect(html.classList.contains('dark')).toBe(false)
  })

  it('reads a dark mode saved by an earlier build back as light', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ state: { mode: 'dark', color: 'zinc', appMode: 'live' }, version: 0 })
    )
    html.classList.add('dark')

    await useThemeStore.persist.rehydrate()

    expect(useThemeStore.getState().mode).toBe('light')
    expect(html.classList.contains('dark')).toBe(false)
  })

  it('keeps a saved Analyze mode across a reload, still without dark', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ state: { mode: 'dark', color: 'zinc', appMode: 'analyzer' }, version: 0 })
    )

    await useThemeStore.persist.rehydrate()

    expect(useThemeStore.getState().appMode).toBe('analyzer')
    expect(html.classList.contains('analyzer')).toBe(true)
    expect(html.classList.contains('dark')).toBe(false)
  })
})
