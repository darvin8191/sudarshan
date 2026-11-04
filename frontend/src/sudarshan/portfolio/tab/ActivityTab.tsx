import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { PortfolioError, portfolioCall, problemOf } from '../api'
import { SECONDARY } from '../sheets/TabFields'
import type { Account, Activity, Asset, TabInfo } from '../types'
import { ActivityList } from './ActivityList'

export const PORTFOLIO_KEY = ['sudarshan', 'portfolio'] as const

/** The Activity part of a custom tab: its entries from the service, a page at a time. */
export function ActivityTab({
  tab,
  accounts,
  onAdd,
  onImport,
  onEdit,
  onDuplicate,
}: {
  tab: TabInfo
  accounts: readonly Account[]
  onAdd: () => void
  onImport: () => void
  onEdit: (entry: Activity) => void
  onDuplicate: (entry: Activity) => void
}) {
  const queries = useQueryClient()
  const entries = useInfiniteQuery({
    queryKey: [...PORTFOLIO_KEY, 'activities', tab.id],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      const result = await portfolioCall('activities_list', { id: tab.id, before: pageParam })
      if (!result.enabled)
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      return result.data
    },
    getNextPageParam: (last) => last.next ?? undefined,
    retry: false,
  })
  // The names of the assets the entries speak of, sold out ones too.
  const assets = useQuery({
    queryKey: [...PORTFOLIO_KEY, 'tab', tab.id],
    queryFn: async (): Promise<Asset[]> => {
      const result = await portfolioCall('tab_get', { id: tab.id })
      return result.enabled ? result.data.assets : []
    },
    retry: false,
  })

  const remove = async (entry: Activity) => {
    const result = await portfolioCall('activity_delete', { id: entry.id, version: tab.version })
    if (!result.enabled)
      throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
    await queries.invalidateQueries({ queryKey: PORTFOLIO_KEY })
  }

  if (entries.isPending) {
    return (
      <p
        className="flex items-center justify-center gap-2 py-8 text-sm text-[color:var(--text-secondary)]"
        aria-live="polite"
      >
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        Loading the entries
      </p>
    )
  }
  if (entries.isError) {
    return (
      <div
        className="flex flex-col items-center gap-3 rounded-[1.125rem] border bg-card px-6 py-8 text-center shadow-[var(--shadow-card)]"
        aria-live="polite"
      >
        <TriangleAlert aria-hidden="true" className="size-6 text-[color:var(--text-secondary)]" />
        <p className="text-base font-bold">{problemOf(entries.error).message}</p>
        <button type="button" className={SECONDARY} onClick={() => entries.refetch()}>
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </div>
    )
  }
  return (
    <ActivityList
      items={entries.data.pages.flatMap((page) => page.items)}
      assets={assets.data ?? []}
      accounts={accounts}
      hasMore={entries.hasNextPage}
      loadingMore={entries.isFetchingNextPage}
      onMore={() => entries.fetchNextPage()}
      onAdd={onAdd}
      onImport={onImport}
      onEdit={onEdit}
      onDuplicate={onDuplicate}
      onDelete={remove}
    />
  )
}
