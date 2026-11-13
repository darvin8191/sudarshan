/** The query keys of the journal, so a write can refresh what it changed and nothing else. */
export const JOURNAL_KEY = ['sudarshan', 'journal'] as const
export const JOURNAL_STATE_KEY = [...JOURNAL_KEY, 'state'] as const
