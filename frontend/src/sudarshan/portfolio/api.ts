// SUDARSHAN M7.18: the portfolio, through OpenAlgo's bridge (blueprints/sudarshan_portfolio.py).
// One function carries every op: the route, the method, the query and the class of op come from
// the contract's table (OPS in types.ts), so a later op never means editing this file. An answer
// is the contract's envelope. Switched off is a success that says so; an error carries a code the
// page acts on and a sentence for the owner, shown as it is.
import axios from 'axios'
import { webClient } from '@/api/client'
import {
  API_PREFIX,
  type CaDecideArgs,
  type CaListArgs,
  type CalendarArgs,
  type DividendSetArgs,
  type DividendsArgs,
  type ErrorCode,
  type ExportArgs,
  type ExportFile,
  type ExportPdf,
  type GoalCreateArgs,
  type GoalUpdateArgs,
  OPS,
  type OpName,
  type OpSpec,
  type PortfolioOps,
  type PortfolioState,
  type ReportCsvArgs,
  type RiskArgs,
  type SettingsSetArgs,
  SWITCH_NAMES,
  type SwitchName,
  type TaxCsvArgs,
  type TaxGetArgs,
  type TaxInputDeleteArgs,
  type TaxInputSetArgs,
  type TaxInputsArgs,
  WORDS,
} from './types'

/** A sentence for the owner and the code the page acts on. */
export interface PortfolioProblem {
  code: ErrorCode | 'unanswered' | 'unsent' | 'unreadable' | 'too_large'
  message: string
  /** `refused`: the entry that stands in the way. */
  entryId: number | null
}

export class PortfolioError extends Error {
  readonly code: PortfolioProblem['code']
  readonly entryId: number | null
  constructor(problem: PortfolioProblem) {
    super(problem.message)
    this.name = 'PortfolioError'
    this.code = problem.code
    this.entryId = problem.entryId
  }
}

/** What an op returned: its data, or that the portfolio is switched off (with the sentence). */
export type PortfolioResult<T> =
  | { enabled: true; data: T }
  | { enabled: false; data: null; message: string }

const NO_ANSWER = 'No answer came from OpenAlgo. Check the connection, then try again.'
const NOT_SENT = 'The request could not be sent. Reload the page, then try again.'
/** What the server's front door answers (as a page, not as JSON) to a file it refuses (413). */
export const TOO_LARGE =
  'This file is larger than the server accepts. Split it into smaller files and import them one by one.'
const UNREADABLE = 'OpenAlgo’s answer could not be read. Try again in a minute.'

/** Waits for the bridge's own waits (3, 6, 15 and 12 s) with room for the trip. */
const WAIT_MS = { read: 15_000, write: 20_000, import: 30_000, upstox: 25_000 } as const

const SPECS = new Map<OpName, OpSpec>(OPS.map((spec) => [spec.name, spec]))

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/** The sentence an error code has in the contract, when the answer carried none. */
function wordsFor(code: string): string | null {
  const words: Record<string, string> = WORDS
  return text(words[code])
}

function problemFrom(body: unknown): PortfolioProblem | null {
  if (!body || typeof body !== 'object') return null
  const error = (body as { error?: unknown }).error
  if (!error || typeof error !== 'object') return null
  const { code, message, entry_id } = error as Record<string, unknown>
  const known = text(code)
  const sentence = text(message) ?? (known ? wordsFor(known) : null)
  if (!known || !sentence) return null
  return {
    code: known as ErrorCode,
    message: sentence,
    entryId: typeof entry_id === 'number' ? entry_id : null,
  }
}

const SESSION_ENDED: PortfolioProblem = {
  code: 'unauthorized',
  message: WORDS.unauthorized,
  entryId: null,
}

const asText = (data: unknown): string =>
  typeof data === 'string' ? data : data && typeof data === 'object' ? JSON.stringify(data) : ''

function onLoginPage(url?: string): boolean {
  if (!url) return false
  try {
    return /^\/(auth\/)?login\/?$/.test(new URL(url, window.location.origin).pathname)
  } catch {
    return false
  }
}

/**
 * Whether an answer says the login is over: a 401, a CSRF refusal (a session that expired at
 * 03:00 fails the CSRF check first, as JSON or as a page), or a redirect that landed on the login
 * page (a form upload is answered that way and the browser follows it).
 */
export function sessionEnded(status: number | undefined, data: unknown, url?: string): boolean {
  if (status === 401) return true
  if (onLoginPage(url)) return true
  if (status === 400 || status === 403) return /csrf/i.test(asText(data))
  return false
}

/** The problem in an error a request threw, in the owner's words. */
export function problemOf(error: unknown): PortfolioProblem {
  if (error instanceof PortfolioError) {
    return { code: error.code, message: error.message, entryId: error.entryId }
  }
  if (!axios.isAxiosError(error)) {
    return { code: 'unsent', message: NOT_SENT, entryId: null }
  }
  if (!error.response) return { code: 'unanswered', message: NO_ANSWER, entryId: null }
  const { status, data, request } = error.response as {
    status: number
    data: unknown
    request?: { responseURL?: string }
  }
  const coded = problemFrom(data)
  if (coded === null && sessionEnded(status, data, request?.responseURL)) return SESSION_ENDED
  if (coded === null && status === 413) {
    return { code: 'too_large', message: TOO_LARGE, entryId: null }
  }
  return (
    problemFrom(error.response.data) ?? { code: 'unreadable', message: UNREADABLE, entryId: null }
  )
}

function queryOf(spec: OpSpec, args: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [name] of spec.query ?? []) {
    const value = args[name]
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value) && value.length === 0) continue
    out[name] = Array.isArray(value) ? value.join(',') : String(value)
  }
  return out
}

function pathOf(spec: OpSpec, args: Record<string, unknown>, base: string = API_PREFIX): string {
  const id = args.id
  return `${base}${spec.route.replace('<id>', String(id ?? ''))}`
}

function bodyOf(args: Record<string, unknown>): Record<string, unknown> {
  const { id: _target, ...rest } = args
  return rest
}

function uploadOf(args: Record<string, unknown>): FormData {
  const { id: _target, text: content, filename, ...fields } = args
  const form = new FormData()
  const name = typeof filename === 'string' && filename ? filename : 'import.csv'
  form.append('file', new Blob([String(content ?? '')], { type: 'text/csv' }), name)
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== null) form.append(key, String(value))
  }
  return form
}

/** One op at the bridge. Throws a `PortfolioError` in the owner's words. */
export async function portfolioCall<N extends OpName>(
  name: N,
  args?: PortfolioOps[N]['args'],
  signal?: AbortSignal,
  // SUDARSHAN P6 D6 (M7.41, M7.37): the base path the route is added to. The owner's page never
  // passes it. A reader of a shared portfolio through the family service's own copy of these
  // routes passes `/family/api/portfolio`, and only a read op may be sent there (the family
  // purpose accepts reads and nothing else).
  options: { base?: string } = {}
): Promise<PortfolioResult<PortfolioOps[N]['data']>> {
  const spec = SPECS.get(name)
  if (!spec) throw new PortfolioError({ code: 'unsent', message: NOT_SENT, entryId: null })
  const given = (args ?? {}) as Record<string, unknown>
  const url = pathOf(spec, given, options.base)
  const timeout = WAIT_MS[spec.cls]
  let body: unknown
  try {
    const response =
      spec.method === 'GET'
        ? await webClient.get(url, { params: queryOf(spec, given), timeout, signal })
        : await webClient.request({
            url,
            method: spec.method,
            data: spec.upload ? uploadOf(given) : bodyOf(given),
            timeout,
            signal,
          })
    if (sessionEnded(undefined, null, response.request?.responseURL)) {
      throw new PortfolioError(SESSION_ENDED)
    }
    body = response.data
  } catch (error) {
    throw new PortfolioError(problemOf(error))
  }
  const answer = body as { status?: unknown; enabled?: unknown; data?: unknown; message?: unknown }
  if (answer && typeof answer === 'object' && answer.status === 'success') {
    if (answer.enabled === false) {
      return { enabled: false, data: null, message: text(answer.message) ?? WORDS.off }
    }
    if (answer.enabled === true && answer.data !== undefined && answer.data !== null) {
      return { enabled: true, data: answer.data as PortfolioOps[N]['data'] }
    }
  }
  throw new PortfolioError(
    problemFrom(body) ?? { code: 'unreadable', message: UNREADABLE, entryId: null }
  )
}

// ---- SUDARSHAN P7 F0 (M7.27): phase P7's ops, one function each ----
// The views of tax, insights and goals land one at a time in their own folders and call these, so
// none of them edits this file. Each answers `not_ready` until its part is built and while its
// switch is off on the server.

/** Which of phase P7's switches the service says are on; a switch it does not name is off. */
export function switchesOf(state: PortfolioState | null | undefined): Record<SwitchName, boolean> {
  const said: Partial<Record<SwitchName, unknown>> = state?.switches ?? {}
  const out = {} as Record<SwitchName, boolean>
  for (const name of SWITCH_NAMES) out[name] = said[name] === true
  return out
}

export const getTax = (args: TaxGetArgs = {}, signal?: AbortSignal) =>
  portfolioCall('tax_get', args, signal)
export const getTaxInputs = (args: TaxInputsArgs = {}, signal?: AbortSignal) =>
  portfolioCall('tax_inputs', args, signal)
export const saveTaxInput = (args: TaxInputSetArgs) => portfolioCall('tax_input_set', args)
export const deleteTaxInput = (args: TaxInputDeleteArgs) => portfolioCall('tax_input_delete', args)
export const getTaxCsv = (args: TaxCsvArgs) => portfolioCall('tax_csv', args)
export const getReportCsv = (args: ReportCsvArgs) => portfolioCall('report_csv', args)
export const listCorporateActions = (args: CaListArgs = {}, signal?: AbortSignal) =>
  portfolioCall('ca_list', args, signal)
export const decideCorporateAction = (args: CaDecideArgs) => portfolioCall('ca_decide', args)
export const getDividends = (args: DividendsArgs = {}, signal?: AbortSignal) =>
  portfolioCall('dividends', args, signal)
export const setDividend = (args: DividendSetArgs) => portfolioCall('dividend_set', args)
export const getCalendar = (args: CalendarArgs = {}, signal?: AbortSignal) =>
  portfolioCall('calendar', args, signal)
export const getRisk = (args: RiskArgs = {}, signal?: AbortSignal) =>
  portfolioCall('risk', args, signal)
export const getSettings = (signal?: AbortSignal) => portfolioCall('settings_get', {}, signal)
export const saveSettings = (args: SettingsSetArgs) => portfolioCall('settings_set', args)
export const listGoals = (signal?: AbortSignal) => portfolioCall('goals_list', {}, signal)
export const createGoal = (args: GoalCreateArgs) => portfolioCall('goal_create', args)
export const updateGoal = (args: GoalUpdateArgs) => portfolioCall('goal_update', args)
export const getExportPdf = (args: ExportArgs) => portfolioCall('export_pdf', args)

/** A downloaded file handed to the phone to keep: CSV text as UTF-8, a PDF from its base64. */
export function saveDownload(file: ExportFile | ExportPdf): void {
  const blob =
    'base64' in file
      ? new Blob([Uint8Array.from(atob(file.base64), (c) => c.charCodeAt(0))], {
          type: 'application/pdf',
        })
      : new Blob([file.text], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = file.filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
