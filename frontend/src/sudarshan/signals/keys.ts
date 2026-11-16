// SUDARSHAN M13 (P8 U1): the TRINETRA view's query keys, one root so a sign-out or a test can drop all.
export const SIGNALS_KEY = ['sudarshan-signals'] as const
export const STATE_KEY = [...SIGNALS_KEY, 'state'] as const
export const TIMELINE_KEY = [...SIGNALS_KEY, 'timeline'] as const
export const SETTINGS_KEY = [...SIGNALS_KEY, 'settings'] as const
export const PERFORMANCE_KEY = [...SIGNALS_KEY, 'performance'] as const
