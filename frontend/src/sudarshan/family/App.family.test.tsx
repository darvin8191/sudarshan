/**
 * Cross-design Y27: the family shell must not boot the owner's app. A viewer phone's nginx
 * refuses every owner path, so a request the shell made to one would show as an error or a retry
 * loop. Rendering any family route may ask only /family/api, /family/ws, /assets, /images and
 * /sw.js: fetch, XMLHttpRequest, WebSocket and beacons are all watched.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetFamilyManagerForTests } from './familyManager'
import { FakeSocket, meOf, ok, routeFetch } from './testing/fixtures'

const ALLOWED = ['/family/api', '/family/ws', '/assets', '/images', '/sw.js']

const asked: string[] = []

function note(raw: unknown) {
  try {
    asked.push(new URL(String(raw), window.location.origin).pathname)
  } catch {
    asked.push(String(raw))
  }
}

class WatchedSocket extends FakeSocket {
  constructor(url: string) {
    super(url)
    note(url)
  }
}

beforeEach(() => {
  asked.length = 0
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', WatchedSocket)
  const open = XMLHttpRequest.prototype.open
  vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(function (
    this: XMLHttpRequest,
    method: string,
    url: string | URL
  ) {
    note(url)
    return open.call(this, method, url)
  } as never)
  Object.defineProperty(navigator, 'sendBeacon', { value: note, configurable: true })
})

afterEach(() => {
  cleanup()
  resetFamilyManagerForTests()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const ROUTES = [
  '/family',
  '/family/positions',
  '/family/holdings',
  '/family/orders',
  '/family/trades',
  '/family/watchlist',
  '/family/portfolio',
  '/family/settings',
]

describe('rendering a family route', () => {
  it.each(
    ROUTES
  )('%s makes requests only to the family service and the app files', async (path) => {
    const router = routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    window.history.pushState({}, '', path)
    // A fresh app each time: the query cache is the app's own and lives as long as the module.
    vi.resetModules()
    const { default: App } = await import('@/App')
    render(<App />)
    await screen.findByTestId('family-badge', undefined, { timeout: 5000 })
    await new Promise((resolve) => setTimeout(resolve, 50))
    for (const url of router.urls()) note(url)
    expect(asked.length).toBeGreaterThan(0)
    for (const path of asked) {
      expect(ALLOWED.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))).toBe(true)
    }
    expect(
      asked.some((path) => /\/auth\/|\/api\/websocket|\/api\/config|socket\.io/.test(path))
    ).toBe(false)
  })

  it('does not run the owner session check, with or without a signed-in family member', async () => {
    const router = routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    window.history.pushState({}, '', '/family/positions')
    vi.resetModules()
    const { default: App } = await import('@/App')
    render(<App />)
    await screen.findByTestId('family-badge', undefined, { timeout: 5000 })
    expect(router.urls()).not.toContain('/auth/session-status')
  })
})
