import { useSyncExternalStore } from 'react'

/**
 * The phone layout: narrower than Tailwind's `md` breakpoint, the width below
 * which the app shows its bottom bar. Asked as the phone condition itself, so
 * a browser (or a test) that cannot answer it gets the wide layout, upstream's.
 */
export const PHONE_QUERY = '(width < 48rem)'

/**
 * A phone on its side (C10): wider than tall, and too short for the upright
 * chart's header, timeframes and footer to leave a chart worth reading. A
 * tablet on its side is tall enough to keep the upright layout.
 */
export const SIDEWAYS_QUERY = '(orientation: landscape) and (height < 30rem)'

/** A media query as a store React can read, answering no where it cannot be asked. */
function mediaStore(query: string) {
  const list = (): MediaQueryList | null => {
    try {
      return typeof window.matchMedia === 'function' ? window.matchMedia(query) : null
    } catch {
      return null
    }
  }
  return {
    subscribe(onChange: () => void): () => void {
      const media = list()
      media?.addEventListener?.('change', onChange)
      return () => media?.removeEventListener?.('change', onChange)
    },
    matches: () => list()?.matches === true,
  }
}

const phone = mediaStore(PHONE_QUERY)
const sideways = mediaStore(SIDEWAYS_QUERY)

/** True on a phone-width screen; follows the width as it changes (a phone turned on its side). */
export function usePhoneLayout(): boolean {
  return useSyncExternalStore(phone.subscribe, phone.matches, () => false)
}

/** True while a phone is held on its side; follows the phone as it is turned. */
export function usePhoneSideways(): boolean {
  return useSyncExternalStore(sideways.subscribe, sideways.matches, () => false)
}
