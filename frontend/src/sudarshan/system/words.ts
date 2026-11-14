// SUDARSHAN (M20.8): each check of the server's watch in the owner's words. Pure: a check's
// name, level and facts in, a label and one line out. The server's sentences on Telegram
// (sudarshan/alerts/notices.py) say the same things; nothing here names a unit, a port number
// the owner cannot act on, or a status code.
import type { CheckLevel, DrillResult, WatchCheck } from '@/api/system'

export type Group = 'services' | 'market' | 'server' | 'backups' | 'safety' | 'connections'

export interface Line {
  group: Group
  label: string
  detail: string
}

export const GROUPS: { id: Group; title: string }[] = [
  { id: 'services', title: 'Services' },
  { id: 'market', title: 'Market day' },
  { id: 'server', title: 'Server' },
  { id: 'backups', title: 'Backups' },
  { id: 'safety', title: 'Safety settings' },
  { id: 'connections', title: 'Connections' },
]

export const LEVEL_WORDS: Record<CheckLevel, string> = {
  fine: 'Fine',
  warning: 'Look at',
  failing: 'Not working',
  unread: 'Could not be read',
  off: 'Off',
}

const SERVICES: Record<string, string> = {
  feed: 'Price feed',
  alerts: 'Alerts service',
  portfolio: 'Portfolio service',
  screener: 'Screener',
  family: 'Family view',
  journal: 'Journal',
}

const UNREAD = 'Could not be read at the last look.'
const whole = (value: string | undefined): string => (value && /^\d{1,7}$/.test(value) ? value : '')
const decimal = (value: string | undefined): string =>
  value && /^\d{1,6}\.\d$/.test(value) ? value : ''
const plural = (count: string, one: string, many: string): string =>
  `${count} ${count === '1' ? one : many}`

function hours(value: string): string {
  const count = Number(value)
  return count >= 72 ? `${Math.floor(count / 24)} days` : plural(value, 'hour', 'hours')
}

const OFFSITE: Record<string, string> = {
  ok: 'sent',
  failed: 'not sent last time',
  off: 'not set up',
}

const PLACES: Record<string, string> = {
  settings: "SUDARSHAN's settings folder",
  vault: 'The folder of sealed Upstox sign-in details',
  env: "OpenAlgo's settings file",
}

/** What the restore drill line says: the drill's own failing sentence when there is one. */
function drillLine(check: WatchCheck, drill: DrillResult | null): string {
  const facts = check.facts ?? {}
  const days = whole(facts.days)
  const ago = days === '0' ? 'today' : days ? `${plural(days, 'day', 'days')} ago` : ''
  if (check.level === 'fine') return ago ? `Passed ${ago}.` : 'Passed.'
  if (facts.cause === 'never')
    return 'Never done yet. It runs on the first Sunday of the month; the README has the line to run it now.'
  if (facts.cause === 'failed') {
    const words = drill?.outcome === 'failed' && drill.words ? drill.words : ''
    return `FAILED${ago ? ` ${ago}` : ''}. ${words || 'The backups may not restore.'} Have the server looked at.`
  }
  if (facts.cause === 'late') return `Last done ${ago || 'over 100 days ago'}. It is overdue.`
  return UNREAD
}

/** A check as the page shows it, or null for a check this build does not know. */
export function lineOf(check: WatchCheck, drill: DrillResult | null = null): Line | null {
  const { name, level } = check
  const facts = check.facts ?? {}
  const unread = level === 'unread'
  if (name.startsWith('service_')) {
    const label = SERVICES[name.slice(8)]
    if (!label) return null
    const minutes = whole(facts.minutes)
    const detail = unread
      ? UNREAD
      : level === 'off'
        ? 'Not set up on this server.'
        : level === 'failing'
          ? `Not answering${minutes ? ` for ${minutes} minutes` : ''}. The server restarts it by itself.`
          : 'Answering.'
    return { group: 'services', label, detail }
  }
  switch (name) {
    case 'openalgo':
      return {
        group: 'services',
        label: 'OpenAlgo',
        detail: unread ? UNREAD : level === 'failing' ? 'Not answering.' : 'Answering.',
      }
    case 'login':
      return {
        group: 'market',
        label: 'Upstox login',
        detail: unread
          ? UNREAD
          : level === 'off'
            ? 'Not looked at now: the market is closed.'
            : level === 'failing'
              ? facts.when === 'before_open'
                ? 'No login for today. Connect Upstox in the app.'
                : 'Order updates from Upstox are not arriving.'
              : 'Connected: order updates are arriving.',
      }
    case 'feed':
      return {
        group: 'market',
        label: "Price feed's connection to Upstox",
        detail: unread
          ? UNREAD
          : level === 'off'
            ? 'Not looked at now: the market is closed.'
            : level === 'failing'
              ? 'Not connected. Prices are not Real-time.'
              : 'Connected.',
      }
    case 'instrument_list':
      return {
        group: 'market',
        label: 'Instrument list',
        detail: unread
          ? UNREAD
          : level === 'off'
            ? 'Looked at from 09:00 on a trading day.'
            : level === 'failing'
              ? 'Not refreshed today. Open Master Contract and press Download.'
              : 'Refreshed today.',
      }
    case 'disk': {
      const free = decimal(facts.free_gb)
      const short = facts.cause === 'files' ? ' Short of room for new files.' : ''
      return {
        group: 'server',
        label: 'Disk',
        detail: unread
          ? UNREAD
          : `${free ? `${free} GB free.` : 'Free space read.'}${level === 'fine' ? '' : short || ' Getting full.'}`,
      }
    }
    case 'memory': {
      const free = whole(facts.available_mb)
      return {
        group: 'server',
        label: 'Memory',
        detail: unread ? UNREAD : free ? `${free} MB available.` : 'Read.',
      }
    }
    case 'certificate': {
      const left = whole(facts.hours)
      return {
        group: 'server',
        label: 'Security certificate',
        detail: unread
          ? UNREAD
          : left === '0'
            ? 'Run out and not renewed. Phones refuse the app.'
            : left
              ? `${hours(left)} left${level === 'failing' ? ' and not renewed.' : '.'}`
              : 'Read.',
      }
    }
    case 'posture_updates': {
      const all = whole(facts.all)
      const security = whole(facts.security)
      return {
        group: 'server',
        label: 'System updates',
        detail: unread
          ? 'This server keeps no count of updates waiting.'
          : `${all || '0'} waiting, ${security || '0'} of them security. Yours to apply by hand on a non-trading day.`,
      }
    }
    case 'posture_reboot':
      return {
        group: 'server',
        label: 'Restart',
        detail: unread
          ? UNREAD
          : level === 'warning'
            ? 'The system asks for a restart. Yours to do on a non-trading day.'
            : 'Not asked for.',
      }
    case 'historify': {
      const size = decimal(facts.gb)
      return {
        group: 'server',
        label: "Historify's downloaded history",
        detail: unread
          ? UNREAD
          : level === 'off'
            ? 'Nothing downloaded.'
            : `${size || '?'} GB.${level === 'warning' ? ' Delete what you no longer need on the Historify page.' : ''}`,
      }
    }
    case 'backup': {
      if (unread) return { group: 'backups', label: 'Nightly backup', detail: UNREAD }
      if (level === 'off')
        return { group: 'backups', label: 'Nightly backup', detail: 'No backup has been made yet.' }
      const age = whole(facts.age_h)
      const size = whole(facts.size_mb)
      const offsite = OFFSITE[facts.offsite ?? '']
      const year =
        facts.fy && /^\d{4}-\d{2}$/.test(facts.fy)
          ? ` The ${facts.fy} year's archive is kept${whole(facts.fy_mb) ? ` (${whole(facts.fy_mb)} MB)` : ''}.`
          : ''
      // A year's archive whose offsite copy has not gone yet (review L4): said until it has.
      const owed =
        facts.fy_owed && /^\d{4}-\d{2}$/.test(facts.fy_owed)
          ? ` The ${facts.fy_owed} year's archive has no offsite copy yet. It is sent again at the next backup.`
          : ''
      return {
        group: 'backups',
        label: 'Nightly backup',
        detail: `Newest is ${age ? hours(age) : 'some time'} old${size ? ` (${size} MB)` : ''}.${offsite ? ` Offsite copy: ${offsite}.` : ''}${year}${owed}`,
      }
    }
    case 'drill':
      return {
        group: 'backups',
        label: 'Restore test',
        detail: unread ? UNREAD : drillLine(check, drill),
      }
    case 'posture_locks':
      return {
        group: 'safety',
        label: "AI assistant's safety settings",
        detail: unread
          ? UNREAD
          : level === 'fine'
            ? 'As shipped: the assistant can only read.'
            : facts.cause === 'approval'
              ? 'A new AI client would be approved without asking you. Set MCP_OAUTH_REQUIRE_APPROVAL=True.'
              : 'Not as shipped, so the assistant stays switched off. Set OPENALGO_MCP_READ_ONLY=1 and MCP_OAUTH_WRITE_SCOPE_ENABLED=False.',
      }
    case 'posture_debug':
      return {
        group: 'safety',
        label: "OpenAlgo's debug setting",
        detail: unread
          ? UNREAD
          : level === 'fine'
            ? 'Off.'
            : 'On. Set FLASK_DEBUG=False and restart OpenAlgo.',
      }
    case 'posture_modes':
      return {
        group: 'safety',
        label: 'Private folders',
        detail: unread
          ? UNREAD
          : level === 'fine'
            ? 'Closed to other accounts on the server.'
            : `${PLACES[facts.what ?? ''] ?? 'A private folder'} can be read by other accounts. Have its permissions set right.`,
      }
    case 'posture_ports':
      return {
        group: 'safety',
        label: "SUDARSHAN's services",
        detail: unread
          ? UNREAD
          : level === 'fine'
            ? 'Listening inside the server only.'
            : 'One is listening on the network. Have the server looked at.',
      }
    case 'assistant': {
      const clients = whole(facts.clients)
      const signedIn = whole(facts.families)
      return {
        group: 'connections',
        label: 'AI connections',
        detail: unread
          ? UNREAD
          : level === 'off'
            ? 'None.'
            : `${clients || '0'} approved, ${signedIn || '0'} signed in.`,
      }
    }
    case 'telegram_links': {
      const links = whole(facts.links)
      return {
        group: 'connections',
        label: 'Telegram accounts linked to your bot',
        detail: unread ? UNREAD : links ? `${links}.` : 'Read.',
      }
    }
    default:
      return null
  }
}

const clock = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const day = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
})

/** "10:42" for a moment today in India, "14 Nov, 10:42" for another day; '' without one. */
export function whenOf(seconds: number | null | undefined, now: number = Date.now()): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return ''
  const at = new Date(seconds * 1000)
  const sameDay = day.format(at) === day.format(new Date(now))
  return sameDay ? clock.format(at) : `${day.format(at)}, ${clock.format(at)}`
}
