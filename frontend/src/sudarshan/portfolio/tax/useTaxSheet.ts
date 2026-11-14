// SUDARSHAN P7 F2 (M7.27): which sheet of the Tax view is open, kept in the address
// (`sheet=tax-sales`, `sheet=tax-inputs`) so the phone's Back closes the sheet first and a reload
// keeps it. Opening pushes one entry; closing goes back over it, or drops the name when the page
// was opened straight on the sheet.
import { useLocation, useNavigate, useSearchParams } from 'react-router'

export const TAX_SHEETS = ['tax-sales', 'tax-inputs'] as const
export type TaxSheet = (typeof TAX_SHEETS)[number]

export const useTaxSheet = () => useAddressSheet(TAX_SHEETS)

/** One of `names` open as a sheet, kept in the address under `sheet`. */
export function useAddressSheet<N extends string>(
  names: readonly N[]
): {
  sheet: N | null
  open: (name: N) => void
  close: () => void
} {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const sheet = names.find((name) => name === params.get('sheet')) ?? null
  const pushed = (location.state as { sdTaxSheet?: unknown } | null)?.sdTaxSheet === true
  const go = (name: N | null, replace: boolean) => {
    const after = new URLSearchParams(params)
    if (name) after.set('sheet', name)
    else after.delete('sheet')
    navigate(
      { search: `?${after.toString()}` },
      { replace, state: name ? { sdTaxSheet: true } : null }
    )
  }
  return {
    sheet,
    open: (name) => {
      if (sheet === null) go(name, false)
    },
    close: () => {
      if (sheet === null) return
      if (pushed) navigate(-1)
      else go(null, true)
    },
  }
}
