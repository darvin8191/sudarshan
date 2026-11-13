import { useQuery } from '@tanstack/react-query'
import { journalData } from '../journal/api'
import type { ReportRow, SettingsRead } from './words'

export const REPORTS_KEY = ['sudarshan', 'reports'] as const

/**
 * The archive is not market data: it is read when the page opens and when the owner comes back to
 * it (a tab shown again, the phone woken), never on a timer.
 */
export function useArchive() {
  return useQuery({
    queryKey: [...REPORTS_KEY, 'list'],
    queryFn: async () => (await journalData('reports_list', { limit: 100 })) as ReportRow[],
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  })
}

export function useReportSettings() {
  return useQuery({
    queryKey: [...REPORTS_KEY, 'settings'],
    queryFn: async () => (await journalData('settings_get', {})) as SettingsRead,
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: true,
  })
}
