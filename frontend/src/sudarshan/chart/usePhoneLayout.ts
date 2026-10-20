import { useSyncExternalStore } from 'react'

/**
 * The phone layout: narrower than Tailwind's `md` breakpoint, the width below
 * which the app shows its bottom bar. Asked as the phone condition itself, so
 * a browser (or a test) that cannot answer it gets the wide layout, upstream's.
 */
export const PHONE_QUERY = '(width < 48rem)'

function phoneQuery(): MediaQueryList | null {
  try {
    return typeof window.matchMedia === 'function' ? window.matchMedia(PHONE_QUERY) : null
  } catch {
    return null
  }
}

function subscribe(onChange: () => void): () => void {
  const list = phoneQuery()
  list?.addEventListener?.('change', onChange)
  return () => list?.removeEventListener?.('change', onChange)
}

const isPhone = () => phoneQuery()?.matches === true

/** True on a phone-width screen; follows the width as it changes (a phone turned on its side). */
export function usePhoneLayout(): boolean {
  return useSyncExternalStore(subscribe, isPhone, () => false)
}
