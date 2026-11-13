/**
 * The analytics filter row, kept in the address (`period`, `from`, `to`, `strategy`, `playbook`,
 * `tag`, `expiry`) so a reload keeps it and a move between parts of the page does not lose it, and
 * turned into the `analytics` op's request. The mode is not here: the page's mode is on every read.
 */
import type { AnalyticsReq, Mode } from './types'

export const PERIODS = [
  { id: 'today', label: 'Today' },
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
  { id: 'fy', label: 'FY' },
  { id: 'all', label: 'All' },
  { id: 'custom', label: 'Custom' },
] as const
export type Period = (typeof PERIODS)[number]['id']

export const EXPIRY_CHOICES = [
  { id: '', label: 'Every day' },
  { id: 'expiry', label: 'Expiry day only' },
  { id: 'non_expiry', label: 'Non-expiry days only' },
] as const
export type ExpiryChoice = (typeof EXPIRY_CHOICES)[number]['id']

export interface AnalyticsFilter {
  period: Period
  /** YYYYMMDD, for a custom period. */
  from: number | null
  to: number | null
  strategy: string
  playbook: number | null
  tag: number | null
  expiry: ExpiryChoice
}

export const DEFAULT_PERIOD: Period = 'month'

const KEYS = ['period', 'from', 'to', 'strategy', 'playbook', 'tag', 'expiry'] as const

const dayOf = (raw: string | null): number | null => {
  if (raw === null || !/^\d{8}$/.test(raw)) return null
  const month = Math.floor(Number(raw) / 100) % 100
  const day = Number(raw) % 100
  return month >= 1 && month <= 12 && day >= 1 && day <= 31 ? Number(raw) : null
}

const idOf = (raw: string | null): number | null =>
  raw !== null && /^[0-9]{1,9}$/.test(raw) && Number(raw) > 0 ? Number(raw) : null

export function filterOf(params: URLSearchParams): AnalyticsFilter {
  const period = params.get('period')
  const expiry = params.get('expiry')
  return {
    period: PERIODS.some((p) => p.id === period) ? (period as Period) : DEFAULT_PERIOD,
    from: dayOf(params.get('from')),
    to: dayOf(params.get('to')),
    strategy: (params.get('strategy') ?? '').slice(0, 40),
    playbook: idOf(params.get('playbook')),
    tag: idOf(params.get('tag')),
    expiry: EXPIRY_CHOICES.some((c) => c.id === expiry) ? (expiry as ExpiryChoice) : '',
  }
}

/** The address with this filter in it; anything at its default is left out. */
export function withFilter(params: URLSearchParams, filter: AnalyticsFilter): URLSearchParams {
  const out = new URLSearchParams(params)
  for (const key of KEYS) out.delete(key)
  if (filter.period !== DEFAULT_PERIOD) out.set('period', filter.period)
  if (filter.period === 'custom') {
    if (filter.from !== null) out.set('from', String(filter.from))
    if (filter.to !== null) out.set('to', String(filter.to))
  }
  if (filter.strategy) out.set('strategy', filter.strategy)
  if (filter.playbook !== null) out.set('playbook', String(filter.playbook))
  if (filter.tag !== null) out.set('tag', String(filter.tag))
  if (filter.expiry) out.set('expiry', filter.expiry)
  return out
}

/**
 * The request for a filter. A named period goes as its name, for the service to measure in India's
 * calendar; a custom one as its two days. A custom period with a day missing asks for nothing
 * yet (null): the page says to pick both days.
 */
export function requestOf(mode: Mode, filter: AnalyticsFilter): AnalyticsReq | null {
  const req: AnalyticsReq = { mode }
  if (filter.period === 'custom') {
    if (filter.from === null || filter.to === null) return null
    req.from = Math.min(filter.from, filter.to)
    req.to = Math.max(filter.from, filter.to)
  } else {
    req.period = filter.period
  }
  if (filter.strategy) req.strategy = filter.strategy
  if (filter.playbook !== null) req.playbook = filter.playbook
  if (filter.tag !== null) req.tag = filter.tag
  if (filter.expiry) req.expiry = filter.expiry
  return req
}

/** How many filters beyond the period are on. */
export const narrowedBy = (filter: AnalyticsFilter): number =>
  [
    filter.strategy !== '',
    filter.playbook !== null,
    filter.tag !== null,
    filter.expiry !== '',
  ].filter(Boolean).length

/** A day as the native date field reads it (2026-11-13), and back. */
export const dayToField = (day: number | null): string => {
  if (day === null) return ''
  const text = String(day)
  return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
}
export const fieldToDay = (field: string): number | null => dayOf(field.replaceAll('-', ''))
