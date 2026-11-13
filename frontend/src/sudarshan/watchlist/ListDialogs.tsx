import { type FormEvent, useEffect, useId, useState } from 'react'
import { watchlistError } from '@/api/watchlist'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { useOpenGuard } from './openGuard'
import { MAX_NAME_LENGTH } from './useWatchlists'

const BUTTON =
  'inline-flex min-h-12 flex-1 items-center justify-center rounded-[0.875rem] px-5 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 sm:flex-none'
const SECONDARY = cn(BUTTON, 'border border-input bg-card hover:bg-accent')

/**
 * Name a new list, or rename the open one. The server's own reason is shown
 * under the box when it refuses (a name already used, the 50-list cap), so the
 * dialog stays open for another try.
 */
export function NameDialog({
  mode,
  current,
  onSubmit,
  onClose,
}: {
  mode: 'create' | 'rename' | 'copy' | null
  /** The open list's name, for a rename; the copy's own name, for Make a copy (F10). */
  current: string
  onSubmit: (name: string) => Promise<void>
  onClose: () => void
}) {
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const errorId = useId()
  // F10: Make a copy is v2 only, so only it ignores taps for its first half second (openGuard.ts).
  const guard = useOpenGuard(mode === 'copy')

  useEffect(() => {
    if (mode !== null) {
      setValue(mode === 'rename' || mode === 'copy' ? current : '')
      setError(null)
    }
  }, [mode, current])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const name = value.trim()
    if (!name || saving) return
    setSaving(true)
    try {
      await onSubmit(name)
      onClose()
    } catch (failure) {
      setError(
        watchlistError(
          failure,
          mode === 'copy' && failure instanceof Error
            ? failure.message // F10: the server's own sentence
            : 'The list could not be saved. Try again.'
        )
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={mode !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        showCloseButton={false}
        className="rounded-[1.125rem] sm:max-w-sm"
        {...(mode === 'copy' ? guard.handlers : {})}
        data-settling={(mode === 'copy' && guard.settling) || undefined}
      >
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>
              {mode === 'rename' ? 'Rename list' : mode === 'copy' ? 'Make a copy' : 'New list'}
            </DialogTitle>
            <DialogDescription>
              {mode === 'copy'
                ? 'The copy is a list of your own that you can change.'
                : 'Every list needs a name of its own.'}
            </DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            aria-label="List name"
            value={value}
            maxLength={MAX_NAME_LENGTH}
            placeholder="For example, Bank stocks"
            onChange={(event) => {
              setValue(event.target.value)
              setError(null)
            }}
            aria-invalid={error !== null}
            aria-describedby={error ? errorId : undefined}
            className="h-12 text-base"
          />
          {error && (
            <p
              id={errorId}
              role="alert"
              className="-mt-2 text-sm font-medium text-[color:var(--down)]"
            >
              {error}
            </p>
          )}
          <DialogFooter className="flex-row gap-2">
            <button type="button" onClick={onClose} className={SECONDARY}>
              Cancel
            </button>
            <button
              type="submit"
              disabled={!value.trim() || saving}
              className={cn(BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
            >
              {saving ? 'Saving' : 'Save'}
            </button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** Delete the open list, after saying what goes with it. */
export function DeleteDialog({
  list,
  onConfirm,
  onClose,
}: {
  list: { name: string; count: number } | null
  onConfirm: () => Promise<void>
  onClose: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (list) setError(null)
  }, [list])

  const confirm = async () => {
    setDeleting(true)
    try {
      await onConfirm()
      onClose()
    } catch (failure) {
      setError(watchlistError(failure, 'The list could not be deleted. Try again.'))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Dialog open={list !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent showCloseButton={false} className="rounded-[1.125rem] sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Delete “{list?.name}”?</DialogTitle>
          <DialogDescription>
            {list && list.count > 0
              ? `Its ${list.count} ${list.count === 1 ? 'symbol goes' : 'symbols go'} with it. This cannot be undone.`
              : 'This cannot be undone.'}
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p role="alert" className="text-sm font-medium text-[color:var(--down)]">
            {error}
          </p>
        )}
        <DialogFooter className="flex-row gap-2">
          <button type="button" onClick={onClose} className={SECONDARY}>
            Cancel
          </button>
          <button
            type="button"
            disabled={deleting}
            onClick={() => void confirm()}
            className={cn(BUTTON, 'bg-destructive text-white hover:bg-destructive/90')}
          >
            {deleting ? 'Deleting' : 'Delete'}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
