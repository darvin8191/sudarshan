import {
  BarChart3,
  Bell,
  BellRing,
  Bookmark,
  BookOpen,
  Bot,
  Boxes,
  Briefcase,
  CandlestickChart,
  ChartCandlestick,
  ClipboardList,
  Code2,
  Database,
  FileBarChart,
  FileBarChart2,
  FileStack,
  FileText,
  FlaskConical,
  Gauge,
  Info,
  Key,
  KeyRound,
  Layers,
  Layers3,
  LayoutDashboard,
  type LucideIcon,
  MessageCircle,
  MessageSquare,
  NotebookPen,
  ScanSearch,
  Search,
  Settings,
  ShieldAlert,
  TrendingUp,
  User,
  Users,
  Workflow,
  Wrench,
  Zap,
} from 'lucide-react'

/**
 * SUDARSHAN M1.11 (T1): the phone menu's sections, in this order. Every entry
 * names one; an entry without one is listed under System.
 */
export type NavGroup = 'Trade' | 'Analyse' | 'Automate' | 'System'
export const NAV_GROUPS: readonly NavGroup[] = ['Trade', 'Analyse', 'Automate', 'System']

export interface NavItem {
  href: string
  label: string
  icon: LucideIcon
  /** Served by Flask (not a React route): render as a full-page link. */
  external?: boolean
  /**
   * Desktop navbar only: below this Tailwind breakpoint the item leaves the
   * bar and is listed under More instead. Items without it always stay.
   */
  collapseBelow?: 'lg' | 'xl'
  /** The phone menu's section for it (SUDARSHAN T1). */
  group?: NavGroup
}

// Main navigation items shown in desktop navbar
export const navItems: NavItem[] = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, group: 'System' },
  { href: '/orderbook', label: 'Orderbook', icon: ClipboardList, group: 'Trade' },
  { href: '/tradebook', label: 'Tradebook', icon: FileText, collapseBelow: 'lg', group: 'Trade' },
  { href: '/positions', label: 'Positions', icon: TrendingUp, group: 'Trade' },
  {
    href: '/trading',
    label: 'Trading',
    icon: CandlestickChart,
    collapseBelow: 'lg',
    group: 'Trade',
  },
  { href: '/platforms', label: 'Platforms', icon: Layers, collapseBelow: 'xl', group: 'Automate' },
  { href: '/strategy', label: 'Strategies', icon: Boxes, collapseBelow: 'xl', group: 'Trade' },
  { href: '/logs', label: 'Logs', icon: FileBarChart, collapseBelow: 'xl', group: 'System' },
  { href: '/tools', label: 'Tools', icon: Wrench, collapseBelow: 'xl', group: 'Analyse' },
]

// Items shown in mobile bottom navigation
// SUDARSHAN M8.2: led by the watchlist, as a broker's app is; Tradebook moves to the menu.
export const bottomNavItems: NavItem[] = [
  { href: '/watchlist', label: 'Watchlist', icon: Bookmark },
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/orderbook', label: 'Orders', icon: ClipboardList },
  { href: '/positions', label: 'Positions', icon: TrendingUp },
]

// Paths in bottom nav (for filtering mobile sheet items)
const bottomNavPaths = bottomNavItems.map((item) => item.href)

// Secondary items for mobile sheet (items not in bottom nav)
export const mobileSheetItems = navItems.filter((item) => !bottomNavPaths.includes(item.href))

// Profile dropdown menu items
export const profileMenuItems: NavItem[] = [
  { href: '/profile', label: 'Profile', icon: User, group: 'System' },
  { href: '/apikey', label: 'API Key', icon: Key, group: 'System' },
  // Action Center stays immediately after API Key. It was moved here out of the
  // main navbar on that understanding and a test pins the adjacency, so a new
  // entry goes after it rather than between the two.
  { href: '/action-center', label: 'Action Center', icon: Bell, group: 'System' },
  // SUDARSHAN M4.15: the phone chart, one instrument real-time; /chart opens the last one.
  { href: '/chart', label: 'Chart', icon: ChartCandlestick, group: 'Trade' },
  // SUDARSHAN M10.3: market alerts, at /market-alerts (upstream owns /alerts).
  { href: '/market-alerts', label: 'Alerts', icon: BellRing, group: 'Analyse' },
  // SUDARSHAN M11.1: the option chain on the phone. Under Trade it follows Chart; it sits after
  // Alerts here because Alerts is pinned right after Chart.
  { href: '/options', label: 'Options', icon: Layers3, group: 'Trade' },
  // Agent Config is NOT here. It lives under /admin with the other
  // configuration surfaces. The chat header carries its own settings control,
  // so a configured /agent still has a route back to its settings without this
  // menu holding one.
  { href: '/agent', label: 'Agent', icon: Bot, group: 'System' },
  { href: '/master-contract', label: 'Master Contract', icon: FileStack, group: 'System' },
  // SUDARSHAN M2.20: Upstox auto-login (TOTP + PIN), shown unfiltered like the bots below.
  { href: '/autologin', label: 'Auto login', icon: KeyRound, group: 'System' },
  // SUDARSHAN M20.7: the kill switch and the risk guard.
  { href: '/risk', label: 'Risk', icon: ShieldAlert, group: 'System' },
  // SUDARSHAN P6 D2 (M2.7): the owner's Family page, right after Risk. The viewer's own pages are
  // not listed here: their menu is sudarshan/family/navigation.ts (cross-design Y35, Y36).
  { href: '/family-members', label: 'Family', icon: Users, group: 'System' },
  // SUDARSHAN M12.1: the TradingView strategies whose alerts become orders.
  { href: '/automation', label: 'Automation', icon: Workflow, group: 'Automate' },
  { href: '/telegram', label: 'Telegram Bot', icon: MessageSquare, group: 'System' },
  { href: '/whatsapp', label: 'WhatsApp Bot', icon: MessageCircle, group: 'System' },
  // SUDARSHAN (cross-design Y36): Watchlist, Screener, Portfolio, Journal, Reports, then Holdings.
  // M8.2: the watchlist, for a wide screen where the bottom bar is hidden.
  { href: '/watchlist', label: 'Watchlist', icon: Bookmark, group: 'Trade' },
  // SUDARSHAN M9: the screener, right after the watchlist.
  { href: '/screener', label: 'Screener', icon: ScanSearch, group: 'Analyse' },
  // SUDARSHAN M7.18: the portfolio, right after the screener (never between API Key and Action Center).
  { href: '/portfolio', label: 'Portfolio', icon: Briefcase, group: 'Analyse' },
  // SUDARSHAN M14: the trade journal, right after the portfolio (before Reports and Holdings).
  { href: '/journal', label: 'Journal', icon: NotebookPen, group: 'Analyse' },
  // SUDARSHAN M15.10: the reports archive, right after the journal.
  { href: '/reports', label: 'Reports', icon: FileBarChart2, group: 'Analyse' },
  { href: '/holdings', label: 'Holdings', icon: ClipboardList, group: 'Trade' },
  { href: '/flow', label: 'Flow Editor', icon: Workflow, group: 'Automate' },
  { href: '/scalping', label: 'Scalping', icon: Zap, group: 'Trade' },
  { href: '/python', label: 'Python Strategies', icon: Code2, group: 'Automate' },
  { href: '/pnl-tracker', label: 'PnL Tracker', icon: BarChart3, group: 'System' },
  { href: '/historify', label: 'Historify', icon: Database, group: 'System' },
  { href: '/search/token', label: 'Search', icon: Search, group: 'System' },
  { href: '/sandbox', label: 'Sandbox', icon: FlaskConical, group: 'System' },
  { href: '/leverage', label: 'Leverage', icon: Gauge, group: 'System' },
  { href: '/admin', label: 'Admin', icon: Settings, group: 'System' },
  // SUDARSHAN: version, build and licences (M1.16).
  { href: '/about', label: 'About', icon: Info, group: 'System' },
]

// External links
export const externalLinks = {
  docs: { href: 'https://docs.openalgo.in', label: 'Docs', icon: BookOpen },
}

/**
 * SUDARSHAN T1: where an entry goes on a phone, when not where it goes on a
 * wide screen. The charting terminal is laid out for a wide screen, so on a
 * phone the menu's Trading opens the phone chart; a phone on its side is wide
 * and reaches the terminal (as does the phone chart's "Open in full terminal").
 */
const PHONE_HREF: Readonly<Record<string, string>> = { '/trading': '/chart' }

export interface MenuGroup {
  group: NavGroup
  items: NavItem[]
}

/**
 * The phone menu's sections (SUDARSHAN M1.11, T1): every entry under its
 * group's heading, the groups in NAV_GROUPS' order and the entries in the
 * order given. Nothing is left out: an entry is dropped only when an earlier
 * one goes to the same page, which on a phone is the terminal folding into
 * the phone chart's own entry.
 */
export function menuGroups(items: NavItem[], { phone }: { phone: boolean }): MenuGroup[] {
  const seen = new Set<string>()
  const byGroup = new Map<NavGroup, NavItem[]>()
  for (const item of items) {
    const href = (phone && PHONE_HREF[item.href]) || item.href
    if (seen.has(href)) continue
    seen.add(href)
    const group = item.group ?? 'System'
    const held = byGroup.get(group) ?? []
    held.push(href === item.href ? item : { ...item, href })
    byGroup.set(group, held)
  }
  return NAV_GROUPS.flatMap((group) => {
    const held = byGroup.get(group)
    return held ? [{ group, items: held }] : []
  })
}

/**
 * SUDARSHAN: entries whose page owns addresses under its own (`/options/NFO/NIFTY`). Only these
 * match by prefix; every other entry is a leaf and matches exactly.
 */
export const NAV_WITH_CHILDREN: ReadonlySet<string> = new Set(['/options'])

const owns = (pathname: string, href: string): boolean =>
  pathname === href || (NAV_WITH_CHILDREN.has(href) && pathname.startsWith(`${href}/`))

/**
 * Whether an entry is the one the address belongs to. An exact match, or for an entry that owns
 * child addresses a match on whole path segments (`/optionsx` is not `/options`). Where two
 * entries would both own an address, the longer one alone does, so one address never lights two
 * entries.
 */
export function isActiveRoute(
  pathname: string,
  href: string,
  /** Every entry's address; the app's own lists unless a test says. */
  known: readonly string[] = [...navItems, ...bottomNavItems, ...profileMenuItems].map(
    (item) => item.href
  )
): boolean {
  if (!owns(pathname, href)) return false
  return !known.some((other) => other.length > href.length && owns(pathname, other))
}
