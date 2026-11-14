// SUDARSHAN M12.1: the owner's automation routes (blueprints/sudarshan_automation.py), as the
// contract's table names them (types.ts, generated). Every answer but a lost session is read
// here, refusals included: their sentence is what the page shows.
import { webClient } from '@/api/client'
import {
  type AlertsAnswer,
  AUTOMATION_ROUTES,
  type CheckAnswer,
  type CreateAnswer,
  type ListAnswer,
  type Mode,
  type OneAnswer,
  type OthersAnswer,
  type StrategyForm,
  type StrategyState,
} from './types'

export type Answer<T> = ({ ok: true } & T) | { ok: false; message: string; field?: string }

export const NO_ANSWER = 'No answer came from SUDARSHAN. Check the connection, then try again.'
export const NO_ANSWER_WRITE =
  'No answer came back, so the change may or may not have been made. Look at the strategy before you try again.'
const UNREADABLE = 'SUDARSHAN could not finish this. Try again in a few seconds.'

type Route = (typeof AUTOMATION_ROUTES)[keyof typeof AUTOMATION_ROUTES]

/** One call. A change waits for an alert being handled (up to 30 s on the server). */
export async function ask<T>(route: Route, data?: object): Promise<Answer<T>> {
  const read = route.method === 'GET'
  try {
    const response = await webClient.request({
      url: route.path,
      method: route.method,
      [read ? 'params' : 'data']: data,
      timeout: read ? 15_000 : 45_000,
      validateStatus: (status) => status !== 401,
    })
    const body = response.data
    if (response.status === 200 && body?.status === 'success') return { ok: true, ...body }
    const said = typeof body?.message === 'string' && body.message ? body.message : UNREADABLE
    return {
      ok: false,
      message: said,
      field: typeof body?.field === 'string' ? body.field : undefined,
    }
  } catch {
    return {
      ok: false,
      message: read || route === AUTOMATION_ROUTES.check ? NO_ANSWER : NO_ANSWER_WRITE,
    }
  }
}

/** A read for a query: the data, or the sentence thrown. */
async function read<T>(route: Route, params?: object): Promise<T> {
  const answer = await ask<T>(route, params)
  if (!answer.ok) throw new Error(answer.message)
  return answer
}

export const LIST_KEY = ['sudarshan-automation', 'list'] as const
export const OTHERS_KEY = ['sudarshan-automation', 'others'] as const
export const oneKey = (id: number) => ['sudarshan-automation', 'one', id] as const
export const alertsKey = (id: number) => ['sudarshan-automation', 'alerts', id] as const
export const dayKey = (id: number) => ['sudarshan-automation', 'day', id] as const

export const automationApi = {
  list: () => read<ListAnswer>(AUTOMATION_ROUTES.list),
  others: () => read<OthersAnswer>(AUTOMATION_ROUTES.others),
  one: (id: number) => read<OneAnswer>(AUTOMATION_ROUTES.one, { id }),
  create: (form: StrategyForm) => ask<CreateAnswer>(AUTOMATION_ROUTES.create, form),
  update: (id: number, version: number, changes: Partial<StrategyForm>) =>
    ask<OneAnswer>(AUTOMATION_ROUTES.update, { id, version, changes }),
  /** A new link: the old one stops working. The answer is the only place the link is. */
  newLink: (id: number, version: number) =>
    ask<CreateAnswer>(AUTOMATION_ROUTES.token, { id, version }),
  /** The switch. The phrase is asked for only where a Live strategy is started. */
  setState: (id: number, version: number, state: StrategyState, phrase?: string) =>
    ask<OneAnswer>(AUTOMATION_ROUTES.state, { id, version, state, ...(phrase && { phrase }) }),
  setMode: (id: number, version: number, mode: Mode, phrase?: string) =>
    ask<OneAnswer>(AUTOMATION_ROUTES.mode, { id, version, mode, ...(phrase && { phrase }) }),
  /** One page of a strategy's alert lines, newest first; `before` is the last line's id. */
  alerts: (strategy: number, before?: number) =>
    read<AlertsAnswer>(AUTOMATION_ROUTES.alerts, { strategy, ...(before && { before }) }),
  /** What one alert's text would do now. Nothing is sent. */
  check: (id: number, message: string) =>
    ask<CheckAnswer>(AUTOMATION_ROUTES.check, { id, message }),
}
