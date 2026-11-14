/**
 * A field that holds a secret the owner types (the Auto login key and PIN, the Risk page's
 * confirm phrase): dots instead of characters, and nothing a password manager or the phone's
 * keyboard takes for a password or a word to learn.
 */

/** Dots instead of characters, without type="password" (which invites a password manager). */
export const MASKED = '[-webkit-text-security:disc]'

/** Attributes that keep password managers (and the keyboard's suggestions) away. */
export const NOT_A_PASSWORD = {
  autoComplete: 'off',
  autoCorrect: 'off',
  autoCapitalize: 'off',
  spellCheck: false,
  'data-1p-ignore': true,
  'data-lpignore': 'true',
  'data-bwignore': true,
  'data-form-type': 'other',
} as const
