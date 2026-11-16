/**
 * SUDARSHAN M13 (P8): rendering a TRINETRA screen in a test with the view already read (no request
 * at mount: the state is held fresh in the query cache), or with nothing read yet.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router'
import { STATE_KEY } from '@/sudarshan/signals/keys'
import type { SignalsView } from '@/sudarshan/signals/live'

export function signalsClient(view?: SignalsView): QueryClient {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 60_000 }, mutations: { retry: false } },
  })
  if (view) client.setQueryData(STATE_KEY, view)
  return client
}

export function renderSignals(
  ui: ReactElement,
  {
    view,
    client = signalsClient(view),
    path = '/',
  }: { view?: SignalsView; client?: QueryClient; path?: string } = {}
) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
      </QueryClientProvider>
    ),
  }
}
