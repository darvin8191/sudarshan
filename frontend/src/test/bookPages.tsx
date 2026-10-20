/**
 * The phone books (M6.9) rendered as the app renders them: inside the router,
 * with the app's own query client, a page before them in the history so Back
 * can be pressed and seen, the phone chart's route, and frames run by hand
 * (`frames`). Each test file mocks its own APIs; this holds what they share.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render } from '@testing-library/react'
import type { ReactElement } from 'react'
import {
  type InitialEntry,
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from 'react-router'
import { vi } from 'vitest'
import { createQueryClient } from '@/app/providers'
import { frames } from './frames'

/** The phone's Back and Forward, as the router has them. */
const history = { go: (_delta: number) => {} }

function Nav() {
  const navigate = useNavigate()
  history.go = (delta) => navigate(delta)
  return null
}

function Where() {
  const location = useLocation()
  return (
    <p data-testid="where" data-state={JSON.stringify(location.state ?? null)}>
      {`${location.pathname}${location.search}`}
    </p>
  )
}

function ChartProbe() {
  const { exchange, symbol } = useParams()
  return <p>{`Chart of ${symbol} on ${exchange}`}</p>
}

/**
 * Render a book page at `path`, with the dashboard before it in the history.
 * An entry may carry history state, as one restored by a reload does.
 */
export function renderBook(
  path: string,
  page: ReactElement,
  entries: InitialEntry[] = ['/dashboard', path]
) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Nav />
        <Routes>
          <Route path={path.split('?')[0]} element={page} />
          <Route path="/dashboard" element={<p>Dashboard page</p>} />
          <Route path="/chart/:exchange/:symbol" element={<ChartProbe />} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

export function back(): void {
  act(() => history.go(-1))
}

export function forward(): void {
  act(() => history.go(1))
}

/** Run one animation frame. */
export function frame(): void {
  act(() => frames.flush())
}

/** Let promises settle inside React's act, and draw what they brought. */
export async function settle(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  })
  frame()
}

/** Move the fake clock on, running every timer due on the way, then draw. */
export async function pass(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
  frame()
}

/** Where the router is: path and query. */
export function where(): string {
  return document.querySelector('[data-testid="where"]')?.textContent ?? ''
}

/** The history entry the router is on, state and all, as a reload would restore it. */
export function entryHere(): InitialEntry {
  const probe = document.querySelector('[data-testid="where"]')
  const [pathname, search = ''] = (probe?.textContent ?? '').split(/(?=\?)/)
  return { pathname, search, state: JSON.parse(probe?.getAttribute('data-state') ?? 'null') }
}
