/**
 * The tool registry, shared by the /tools page and the home page.
 *
 * Lives here rather than inside the page so the home page can state how many
 * tools exist without importing the page component -- and so the number is
 * derived rather than typed. A hardcoded count goes stale the moment a tool is
 * added, which is exactly how the previous "15+" survived three new tools.
 */
import {
  Activity,
  ArrowLeftRight,
  BarChart3,
  BarChartHorizontal,
  Calculator,
  ChartColumnBig,
  FolderKanban,
  Gauge,
  History,
  LineChart,
  type LucideIcon,
  Mountain,
  PieChart,
  PiggyBank,
  Shapes,
  Sigma,
  Spline,
  Table2,
  Target,
} from 'lucide-react'

export interface Tool {
  title: string
  description: string
  href: string
  /** SUDARSHAN T2: drawn on the brand tint, one look for every tool (no palette per tile). */
  icon: LucideIcon
  /**
   * SUDARSHAN T2: laid out for a phone, listed first there under "Made for the phone". Upstream's
   * tools are wide-screen pages; the phone's option chain joins with its page (O4).
   */
  phone?: boolean
}

export const tools: Tool[] = [
  {
    title: 'Strategy Builder',
    description:
      'Build multi-leg option strategies with Greeks that move with prices, payoff diagram and what-if simulators',
    href: '/strategybuilder',
    icon: Shapes,
  },
  {
    title: 'Strategy Portfolio',
    description: 'Saved strategies across MyTrades and Simulation watchlists',
    href: '/strategybuilder/portfolio',
    icon: FolderKanban,
  },
  {
    title: 'Portfolio Backtester',
    description:
      'Backtest a weighted portfolio against an index with real delivery costs, rebalancing rules, crisis periods and a full tearsheet',
    href: '/portfolio-backtester',
    icon: History,
  },
  {
    title: 'SIP Backtester',
    description:
      'What a monthly, weekly or quarterly SIP would actually have returned: XIRR, rupee-cost averaging, start-date sensitivity and how it compares with a lumpsum',
    href: '/sip-backtester',
    icon: PiggyBank,
  },
  {
    title: 'Portfolio Analyzer',
    description:
      'Grade the holdings you actually own: concentration, co-movement, drawdown resilience and behaviour in past crises',
    href: '/portfolio-analyzer',
    icon: PieChart,
  },
  {
    title: 'Option Chain',
    description: 'Option chain on ticks, with Greeks, OI data, and quick order placement',
    href: '/optionchain',
    icon: Table2,
  },
  {
    title: 'Option Greeks',
    description: 'Historical IV, Delta, Theta, Vega, and Gamma charts for ATM options',
    href: '/ivchart',
    icon: Sigma,
  },
  {
    title: 'OI Tracker',
    description: 'Open Interest analysis with CE/PE OI bars, PCR overlay, and ATM strike marker',
    href: '/oitracker',
    icon: BarChart3,
  },
  {
    title: 'OI Range',
    description:
      'Open Interest by strike for a custom range with ATM-relative quick selectors and optional 1-minute auto-refresh',
    href: '/oirange',
    icon: BarChartHorizontal,
  },
  {
    title: 'Max Pain',
    description: 'Max Pain strike calculation with visual pain distribution across strikes',
    href: '/maxpain',
    icon: Target,
  },
  {
    title: 'Straddle Chart',
    description:
      'Dynamic ATM Straddle chart with rolling strike, Spot, and Synthetic Futures overlay',
    href: '/straddle',
    icon: LineChart,
  },
  {
    title: 'Straddle PnL',
    description:
      'Simulated intraday ATM straddle P&L with automated N-point adjustments and trade log',
    href: '/straddlepnl',
    icon: Calculator,
  },
  {
    title: 'Vol Surface',
    description: '3D Implied Volatility surface across strikes and expiries from the option chain',
    href: '/volsurface',
    icon: Mountain,
  },
  {
    title: 'GEX Dashboard',
    description: 'Gamma Exposure analysis with OI Walls, Net GEX per strike, and top gamma strikes',
    href: '/gex',
    icon: Gauge,
  },
  {
    title: 'Gamma Density',
    description:
      'Γ×OI density and convexity zones with intraday & to-expiry views, ATM IV, and ±1σ/±2σ expected-move bands',
    href: '/gammadensity',
    icon: Activity,
  },
  {
    title: 'IV Smile',
    description: 'Implied Volatility smile with Call/Put IV curves, ATM IV, and skew analysis',
    href: '/ivsmile',
    icon: Spline,
  },
  {
    title: 'OI Profile',
    description: 'Futures candlestick with OI butterfly and daily OI change across strikes',
    href: '/oiprofile',
    icon: ChartColumnBig,
  },
  {
    title: 'Arbitrage',
    description:
      'Futures calendar-spread scanner across NFO & MCX, ranked by executable bid/ask spread % with one-click two-leg orders',
    href: '/arbitrage',
    icon: ArrowLeftRight,
  },
]
