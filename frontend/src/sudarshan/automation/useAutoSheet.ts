// SUDARSHAN M12.1: what the Automation page has open over its cards, kept in the address
// (`?sheet=new`, `?sheet=edit&id=3`) so the phone's Back closes it before it leaves the page.
import { useCallback } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'

const KINDS = ['new', 'edit', 'setup', 'log', 'switch'] as const
export type SheetKind = (typeof KINDS)[number]
export interface AutoSheet {
  kind: SheetKind
  id: number | null
}

export function useAutoSheet() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const kind = KINDS.find((known) => known === params.get('sheet'))
  const id = Number(params.get('id'))
  const sheet: AutoSheet | null = kind
    ? { kind, id: Number.isInteger(id) && id > 0 ? id : null }
    : null
  // An entry this page pushed is closed by going back; one met by a link or a reload is
  // replaced, so closing never leaves the app. One sheet opened from another takes its entry.
  const mine = (location.state as { sdOverlay?: boolean } | null)?.sdOverlay === true
  const over = sheet !== null
  const open = useCallback(
    (next: SheetKind, strategy?: number) =>
      navigate(`?sheet=${next}${strategy ? `&id=${strategy}` : ''}`, {
        replace: over,
        state: { sdOverlay: over ? mine : true },
      }),
    [navigate, over, mine]
  )
  const close = useCallback(() => {
    if (!over) return
    if (mine) navigate(-1)
    else navigate({ search: '' }, { replace: true })
  }, [navigate, over, mine])
  return { sheet, open, close }
}
