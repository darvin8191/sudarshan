// The tab as the service reads it (tab_get), one query shared by the Overview, the Add sheet
// (which needs the tab's assets and their last prices) and the filters.
import { PortfolioError, portfolioCall } from '../api'
import type { TabGetArgs, TabView } from '../types'
import { PORTFOLIO_KEY } from './ActivityTab'

export const TAB_VIEW_KEY = [...PORTFOLIO_KEY, 'tab-view'] as const

/** The query for one tab; `args` are the filters and the as-of day (they are part of the key). */
export function tabViewOptions(id: number, args: Omit<TabGetArgs, 'id'> = {}) {
  return {
    queryKey: [...TAB_VIEW_KEY, id, args] as const,
    queryFn: async (): Promise<TabView> => {
      const result = await portfolioCall('tab_get', { id, ...args })
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    retry: false,
    refetchOnWindowFocus: false,
  }
}
