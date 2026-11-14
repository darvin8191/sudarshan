// SUDARSHAN P6 D2: the viewer's menu. Viewer pages have no entry in the owner's
// config/navigation.ts by design (cross-design Y35): a viewer never sees the owner's menu, and
// the owner's menu never lists a page a viewer owns. Its own bottom bar is this list.
import {
  Bookmark,
  Briefcase,
  ClipboardList,
  LayoutDashboard,
  type LucideIcon,
  Receipt,
  Settings,
  TrendingUp,
  Wallet,
} from 'lucide-react'
import type { Category, Me } from './types'

export interface FamilyNavItem {
  href: string
  label: string
  icon: LucideIcon
  /** The page is absent (not greyed) for a member whose mask hides this category. */
  needs?: Category
}

/**
 * The Dashboard's address, with its slash. Review M7 (v0.12.0): the installed app's scope and
 * start address are `/family/`, and Chrome treats `/family` as outside it: the app can show its
 * address bar there, and a reload there is not behind the service worker (no offline page). Every
 * link and redirect to the Dashboard uses this.
 */
export const FAMILY_HOME = '/family/'

/** A family address without its last slash, for comparing two of them. */
export const bare = (path: string) => path.replace(/\/+$/, '') || '/'

/** The four on the bar itself. */
export const familyBarItems: FamilyNavItem[] = [
  { href: FAMILY_HOME, label: 'Dashboard', icon: LayoutDashboard },
  { href: '/family/positions', label: 'Positions', icon: TrendingUp },
  { href: '/family/holdings', label: 'Holdings', icon: Wallet },
  { href: '/family/watchlist', label: 'Watchlist', icon: Bookmark },
]

/** The rest, in the sheet behind More. */
export const familyMoreItems: FamilyNavItem[] = [
  { href: '/family/orders', label: 'Orders', icon: ClipboardList, needs: 'orders' },
  { href: '/family/trades', label: 'Trades', icon: Receipt, needs: 'orders' },
  { href: '/family/portfolio', label: 'Portfolio', icon: Briefcase },
  { href: '/family/settings', label: 'Settings', icon: Settings },
]

/** Whether a member's mask hides a page's whole subject. */
function allowed(item: FamilyNavItem, me: Me | null): boolean {
  if (!item.needs) return true
  if (!me) return false
  return !me.hidden.includes(item.needs) && me.privacy[item.needs]
}

/** The bar and the More sheet for a member: pages the mask hides are left out. */
export function familyMenu(me: Me | null): { bar: FamilyNavItem[]; more: FamilyNavItem[] } {
  return {
    bar: familyBarItems.filter((item) => allowed(item, me)),
    more: familyMoreItems.filter((item) => allowed(item, me)),
  }
}

/** The screen title for a path (the app bar carries it). */
export function familyTitle(pathname: string): string {
  const all = [...familyBarItems, ...familyMoreItems]
  const hit = all.find((item) => bare(item.href) === bare(pathname))
  return hit?.label ?? 'Family'
}
