import { useCallback } from 'react'
import { useNavigate } from 'react-router'
import { listAlertAddress } from '@/sudarshan/alerts/useAlertOverlay'
import { useMarketAlertsState } from '@/sudarshan/alerts/useMarketAlertsState'
import { ownId } from '../ListStrip'
import type { RefKind } from './types'

/**
 * "Alert on this list" and "Alert on this section" (M8.14, F17): one item for the list menu and one
 * for each section's menu. The item opens the Alerts page's list form through the address contract
 * (`listAlertAddress`); the service decides what the alert may hold, so the phone only refuses
 * what it knows beforehand, in a sentence, and shows the item disabled with it, never hidden.
 */
export const RANKED_OFF =
  'A ranked list changes with every read. Set the alert on a list or a built-in list instead.'
export const V2_OFF =
  'Alerts on a list need the new watchlist features, which are switched off on the server.'
export const ALERTS_OFF =
  'Market alerts are switched off on the server, so a list cannot be watched.'

/** A section's ref, as the alert service reads it (`list:7/section:2`). */
export const sectionRef = (listRef: string, sectionId: number) => `${listRef}/section:${sectionId}`

export interface AlertOffer {
  /** The Alerts page address, or null while the item is off. */
  href: string | null
  /** Why the item is off, or null. */
  off: string | null
  go: (href: string) => void
}

/** Decides one item: the first reason wins (the feature, then the kind of list, then the alerts). */
export function offerFor(
  list: { ref: string; name: string; count: number; kind: RefKind; fed: boolean },
  on: { v2: boolean; alerts: boolean },
  go: (href: string) => void
): AlertOffer {
  const off = !on.v2 ? V2_OFF : list.kind === 'ranked' ? RANKED_OFF : !on.alerts ? ALERTS_OFF : null
  return {
    href: off === null ? listAlertAddress(list) : null,
    off,
    go,
  }
}

/**
 * The offers of one page: `v2` is the watchlist switch; market alerts are off only when the
 * Alerts state says so (an unread state offers the item, and the Alerts page says what it finds).
 * A list a screener screen fills is told to the form with `fed`, from the screens the page knows.
 */
export function useAlertOffers(v2: boolean, fedIds: ReadonlySet<number>) {
  const navigate = useNavigate()
  const alerts = useMarketAlertsState().data?.enabled !== false
  const go = useCallback((href: string) => navigate(href), [navigate])
  return useCallback(
    (ref: string, name: string, count: number, kind: RefKind = 'list') => {
      const id = ownId(ref.split('/')[0])
      return offerFor(
        { ref, name, count, kind, fed: id !== null && fedIds.has(id) },
        { v2, alerts },
        go
      )
    },
    [v2, alerts, fedIds, go]
  )
}
