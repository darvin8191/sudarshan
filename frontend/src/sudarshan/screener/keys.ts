// The query keys of the screener's cached reads.
export const STATE_KEY = ['sudarshan', 'screener', 'state'] as const
export const RESULTS_KEY = ['sudarshan', 'screener', 'results'] as const
export const resultsKey = (screenId: number) => [...RESULTS_KEY, screenId] as const
export const RUNS_KEY = ['sudarshan', 'screener', 'runs'] as const
export const runsKey = (screenId: number) => [...RUNS_KEY, screenId] as const
export const PRESETS_KEY = ['sudarshan', 'screener', 'presets'] as const
export const UNIVERSES_KEY = ['sudarshan', 'screener', 'universes'] as const
export const TODAY_KEY = ['sudarshan', 'screener', 'today'] as const
export const MEMBERSHIP_KEY = ['sudarshan', 'screener', 'membership'] as const
export const membershipKey = (listId: number) => [...MEMBERSHIP_KEY, listId] as const
