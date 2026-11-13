import { formatRupees } from '@/sudarshan/dashboard/format'
import { PAISE_PER_RUPEE } from './types'

/** Paise as a signed rupee text with a true minus: +₹975.00, −₹195.00. */
export const paiseText = (paise: number): string =>
  formatRupees(paise / PAISE_PER_RUPEE, { signed: true })

/** Paise as a rupee text without a plus: ₹1,234.50. */
export const paiseAmount = (paise: number): string => formatRupees(paise / PAISE_PER_RUPEE)
