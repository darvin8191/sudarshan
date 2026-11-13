// Mounting a family screen the way the app does, for the screens' tests: the family market
// manager in the provider (without it the shared hooks reach for the owner's manager and call
// owner endpoints, which a test must catch), a router, a query client, and a probe that says
// where the page went.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { MarketDataProvider } from '@/contexts/MarketDataContext'
import { getFamilyManager } from '../familyManager'

export const where = { path: '' }

function Probe() {
  where.path = useLocation().pathname
  return null
}

export function mountFamily(
  path: string,
  element: ReactNode,
  route = path.split('?')[0],
  extra: Array<{ path: string; element: ReactNode }> = []
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <MarketDataProvider manager={getFamilyManager()}>
        <MemoryRouter initialEntries={[path]}>
          <Probe />
          <Routes>
            <Route path={route} element={element} />
            {extra.map((each) => (
              <Route key={each.path} path={each.path} element={each.element} />
            ))}
            <Route path="*" element={<p>elsewhere</p>} />
          </Routes>
        </MemoryRouter>
      </MarketDataProvider>
    </QueryClientProvider>
  )
  return { ...view, client }
}

/** A __Host- cookie needs a secure page, which the test page is not: the cookie string is stubbed. */
export function stubCookies(jar: string): void {
  Object.defineProperty(document, 'cookie', {
    configurable: true,
    get: () => jar,
    set: () => undefined,
  })
}
