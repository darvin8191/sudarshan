import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
import type { ReactNode } from 'react'
import { Toaster } from '@/components/ui/sonner'
import { MarketDataProvider } from '@/contexts/MarketDataContext'
import { useAlertStore } from '@/stores/alertStore'

// SUDARSHAN M8.2: the app's query defaults, made by a function so tests run on the real ones.
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 1000 * 60, // 1 minute
        refetchOnWindowFocus: true,
        retry: 1,
      },
    },
  })
}

const queryClient = createQueryClient()

interface ProvidersProps {
  children: ReactNode
}

export function Providers({ children }: ProvidersProps) {
  const { position, maxVisibleToasts, duration } = useAlertStore()

  return (
    <QueryClientProvider client={queryClient}>
      <MarketDataProvider>{children}</MarketDataProvider>
      {/* Light only: without this the toaster follows the phone's system dark setting. */}
      <Toaster
        theme="light"
        position={position}
        richColors
        visibleToasts={maxVisibleToasts}
        duration={duration}
        closeButton
      />
      <ReactQueryDevtools initialIsOpen={false} />
    </QueryClientProvider>
  )
}
