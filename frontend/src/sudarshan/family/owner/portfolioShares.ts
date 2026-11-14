// SUDARSHAN M2.8, M7.37 (cross-design Y6): the portfolio's half of the Family page. Which tabs each
// member is given and each member's privacy floor live in sudarshan-portfolio, and the page writes
// them through OpenAlgo's portfolio bridge (`/sudarshan/portfolio/api/family/*`) from the owner's
// own browser: the family process never writes them, so a break-in there cannot widen what a
// member sees. Every answer comes back as an `OwnerError` in the page's words, so the Family
// page's one-send lock reads it as it reads the family service's.
import { type PortfolioResult, portfolioCall, problemOf } from '@/sudarshan/portfolio/api'
import type {
  MemberPrivacy,
  MemberPrivacySetArgs,
  ShareRemoveArgs,
  ShareRemoved,
  ShareSetArgs,
  SharesList,
  TabShare,
} from '@/sudarshan/portfolio/types'
import { OwnerError, type OwnerProblemCode } from '../ownerApi'
import { CATEGORIES, type MemberCard, type Privacy } from '../types'

/** A share or a floor changed on another screen: the page reads it again at once. */
export const CHANGED_ELSEWHERE =
  'It was changed on another screen, so this was not saved. The page now shows it as it is; make the change again.'
export const NOT_CONFIRMED_HERE =
  'Not confirmed: open the Family page again to see whether it was done.'

/** The portfolio's codes as the Family page's own (the lock acts on these, never shows them). */
const CODES: Readonly<Record<string, OwnerProblemCode>> = {
  bad_request: 'invalid',
  not_found: 'not_found',
  version_conflict: 'conflict',
  refused: 'limit',
  unavailable: 'unavailable',
  not_confirmed: 'not_confirmed',
  unauthorized: 'signed_out',
  forbidden: 'forbidden',
  rate_limited: 'busy',
  not_ready: 'not_ready',
  too_large: 'too_large',
  unanswered: 'unanswered',
  unsent: 'unsent',
  unreadable: 'unreadable',
}

/** A problem of a portfolio call in the Family page's terms. */
export function fromPortfolio(error: unknown): OwnerError {
  if (error instanceof OwnerError) return error
  const problem = problemOf(error)
  const code = CODES[problem.code] ?? 'failed'
  if (code === 'conflict') return new OwnerError(code, CHANGED_ELSEWHERE)
  if (code === 'not_confirmed') return new OwnerError(code, NOT_CONFIRMED_HERE)
  return new OwnerError(code, problem.message)
}

async function dataOf<T>(call: () => Promise<PortfolioResult<T>>): Promise<T> {
  let result: PortfolioResult<T>
  try {
    result = await call()
  } catch (error) {
    throw fromPortfolio(error)
  }
  if (!result.enabled) throw new OwnerError('off', result.message)
  return result.data
}

export const portfolioShares = {
  list: (signal?: AbortSignal): Promise<SharesList> =>
    dataOf(() => portfolioCall('shares_list', {}, signal)),
  set: (args: ShareSetArgs): Promise<TabShare> => dataOf(() => portfolioCall('share_set', args)),
  remove: (args: ShareRemoveArgs): Promise<ShareRemoved> =>
    dataOf(() => portfolioCall('share_remove', args)),
  floor: (args: MemberPrivacySetArgs): Promise<MemberPrivacy> =>
    dataOf(() => portfolioCall('member_privacy_set', args)),
} as const

/** The portfolio's name for a family member: their id as text, as `viewer:<id>` carries it. */
export const memberKey = (id: number): string => String(id)

/** The member's floor in a list, or null when the portfolio has none for them. */
export function floorOf(list: SharesList | undefined, id: number): MemberPrivacy | null {
  return list?.privacy.find((row) => row.member === memberKey(id)) ?? null
}

export type FloorDrift =
  | { kind: 'same' }
  | { kind: 'missing' }
  | { kind: 'differs'; hidesMore: Privacy; showsMore: Privacy; role: boolean }

/** How the portfolio's floor stands against the member's card. */
export function driftOf(floor: MemberPrivacy | null, card: MemberCard): FloorDrift {
  if (floor === null) return { kind: 'missing' }
  const hidesMore = {} as Privacy
  const showsMore = {} as Privacy
  let any = floor.role !== card.role
  for (const c of CATEGORIES) {
    hidesMore[c] = card.privacy[c] && !floor.shown[c]
    showsMore[c] = !card.privacy[c] && floor.shown[c]
    any ||= hidesMore[c] || showsMore[c]
  }
  return any
    ? { kind: 'differs', hidesMore, showsMore, role: floor.role !== card.role }
    : { kind: 'same' }
}
