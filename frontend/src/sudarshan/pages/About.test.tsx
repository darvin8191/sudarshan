import { render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { profileMenuItems } from '@/config/navigation'
import { commitUrl, parseBuildInfo } from '../buildInfo'
import { AboutView } from './About'

const RELEASE = parseBuildInfo({
  VITE_SD_RELEASE: 'v1.2.3',
  VITE_SD_COMMIT: 'ABCDEF1234567890abcdef1234567890abcdef12',
  VITE_SD_SOURCE: 'https://github.com/example/sudarshan/',
})
const DEVELOPMENT = parseBuildInfo({})

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ status: 'success', version: '2.0.2.8', name: 'OpenAlgo' }),
    })
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('build information', () => {
  it('keeps only values of the expected shape', () => {
    expect(RELEASE).toEqual({
      release: 'v1.2.3',
      commit: 'abcdef1234567890abcdef1234567890abcdef12',
      source: 'https://github.com/example/sudarshan',
    })
    expect(commitUrl(RELEASE)).toBe(
      'https://github.com/example/sudarshan/commit/abcdef1234567890abcdef1234567890abcdef12'
    )
    expect(
      parseBuildInfo({
        VITE_SD_RELEASE: 'latest',
        VITE_SD_COMMIT: 'not-a-sha',
        VITE_SD_SOURCE: 'https://evil.example/x',
      })
    ).toEqual({ release: null, commit: null, source: null })
    expect(commitUrl(DEVELOPMENT)).toBeNull()
  })
})

describe('About page', () => {
  it('shows the release, its commit and where its source is', async () => {
    render(<AboutView build={RELEASE} />)

    expect(screen.getByText('सुदर्शन')).toBeInTheDocument()
    expect(screen.getByText('v1.2.3')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'abcdef1' })).toHaveAttribute(
      'href',
      'https://github.com/example/sudarshan/commit/abcdef1234567890abcdef1234567890abcdef12'
    )
    expect(screen.getByRole('link', { name: 'github.com/example/sudarshan' })).toHaveAttribute(
      'href',
      'https://github.com/example/sudarshan'
    )
    await waitFor(() => expect(screen.getByText('OpenAlgo 2.0.2.8')).toBeInTheDocument())
  })

  it('says plainly when a build is not a release', () => {
    render(<AboutView build={DEVELOPMENT} />)

    expect(screen.getByText('A development build, not a release.')).toBeInTheDocument()
    expect(screen.getByText('Not recorded')).toBeInTheDocument()
    expect(screen.getByText('Not recorded in this build')).toBeInTheDocument()
  })

  it('credits the platform and every bundled licence', () => {
    render(<AboutView build={DEVELOPMENT} />)
    const licences = screen.getByText('Licences').closest('[data-slot="card"]') as HTMLElement

    expect(within(licences).getByText(/Built on OpenAlgo \(AGPL-3\.0\)/)).toBeInTheDocument()
    expect(within(licences).getByRole('link', { name: 'OpenAlgo' })).toHaveAttribute(
      'href',
      'https://github.com/marketcalls/openalgo'
    )
    for (const name of ['GNU AGPL-3.0', 'SIL Open Font License 1.1', 'ISC']) {
      expect(within(licences).getByRole('link', { name })).toBeInTheDocument()
    }
    expect(within(licences).getAllByRole('link', { name: 'Apache-2.0' })).toHaveLength(3)
    expect(within(licences).getByText('openalgo-charts')).toBeInTheDocument()
    expect(
      within(licences).getByRole('link', { name: 'SIL Open Font License 1.1' })
    ).toHaveAttribute('href', '/docs/licenses/fonts-OFL.txt')
  })

  it('is reachable from the profile menu', () => {
    expect(profileMenuItems.find((item) => item.href === '/about')?.label).toBe('About')
  })
})
