import { useQueryClient } from '@tanstack/react-query'
import { FileUp } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { BottomSheet } from './BottomSheet'
import {
  bytesOf,
  importTv,
  listNameOf,
  previewTv,
  readFileText,
  type Target,
  tooBig,
} from './tvTransfer'
import { MAX_NAME_LENGTH } from './useWatchlists'
import { problemOf } from './v2/api'
import type { TvDone, TvPreview } from './v2/types'

export const IMPORT_PROMPT =
  'Pick the .txt file you downloaded from TradingView, or paste its text.'

const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-5 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'
const SECONDARY = cn(BUTTON, 'border border-input bg-card hover:bg-accent')
const PRIMARY = cn(BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')

/** The open own list an import can go into, on the version `view` answered; absent for any other list. */
export interface ImportHere {
  id: number
  name: string
  version: string | null
}

/**
 * M8.12 (F15): Import from TradingView (`?sheet=import`). A file or pasted text is previewed
 * (`tv_preview`: nothing is written), then imported on the preview's hash into a new list or, for an
 * own list on screen, into it. Every sentence about the file is the server's, as sent; the preview
 * is dropped whenever what it was made from changes, so Import always sends what was shown.
 */
export function ImportSheet({
  open,
  onClose,
  here,
  onDone,
}: {
  open: boolean
  onClose: () => void
  here: ImportHere | null
  /** The import happened: the server's words and the list to open (the first one made, or this one). */
  onDone: (done: TvDone, opened: number) => void
}) {
  const client = useQueryClient()
  const fileId = useId()
  const picker = useRef<HTMLInputElement>(null)
  const [text, setText] = useState('')
  const [name, setName] = useState('')
  const [into, setInto] = useState<'new' | 'here'>('new')
  const [preview, setPreview] = useState<TvPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)

  useEffect(() => {
    if (open) return
    generation.current += 1
    setText('')
    setName('')
    setInto('new')
    setPreview(null)
    setError(null)
    setBusy(false)
  }, [open])

  const target = (): Target | null =>
    into === 'here' && here
      ? here.version === null
        ? null
        : { list: here.id, version: here.version }
      : { name: name.trim() }

  const stale = () => {
    generation.current += 1
    setPreview(null)
    setError(null)
  }

  const runPreview = async (source: string, named = name) => {
    const dest =
      into === 'here' && here ? { list: here.id } : named.trim() ? { name: named.trim() } : {}
    const said = tooBig(bytesOf(source))
    if (said) return setError(said)
    const mine = ++generation.current
    setBusy(true)
    setError(null)
    setPreview(null)
    try {
      const got = await previewTv(source, dest)
      if (mine === generation.current) setPreview(got)
    } catch (failure) {
      if (mine === generation.current)
        setError(failure instanceof Error ? failure.message : problemOf(failure).message)
    } finally {
      if (mine === generation.current) setBusy(false)
    }
  }

  const pickFile = async (file: File | undefined) => {
    if (!file) return
    stale()
    // Over the limit: said here, before the file is read or anything is sent.
    const said = tooBig(file.size)
    if (said) return setError(said)
    try {
      const read = await readFileText(file)
      // The new list's name comes from the file name, and stays editable.
      const fromFile = listNameOf(file.name).slice(0, MAX_NAME_LENGTH)
      const named = into === 'new' && !name.trim() ? fromFile : name
      setText(read)
      setName(named)
      await runPreview(read, named)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'That file could not be read.')
    }
  }

  const doImport = async () => {
    const dest = target()
    if (!preview || !dest || busy) return
    setBusy(true)
    setError(null)
    const out = await importTv(client, text, preview.hash, dest)
    setBusy(false)
    if (out.done) {
      const first = out.done.lists[0] ?? here?.id
      if (first !== undefined) onDone(out.done, first)
      return
    }
    setError(out.said)
  }

  const matched = preview?.matched ?? 0
  const needsName = into === 'new' && !name.trim()

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Import from TradingView"
      description={IMPORT_PROMPT}
    >
      <div className="flex flex-col gap-3">
        <input
          ref={picker}
          id={fileId}
          type="file"
          accept=".txt,text/plain"
          className="sr-only"
          tabIndex={-1}
          aria-label="TradingView file"
          onChange={(event) => {
            void pickFile(event.target.files?.[0])
            event.target.value = ''
          }}
        />
        <button type="button" className={SECONDARY} onClick={() => picker.current?.click()}>
          <FileUp aria-hidden="true" className="size-5" />
          Choose the .txt file
        </button>
        <textarea
          aria-label="Pasted TradingView text"
          value={text}
          rows={5}
          placeholder="Or paste the text here"
          onChange={(event) => {
            setText(event.target.value)
            stale()
          }}
          className="w-full rounded-[0.875rem] border border-input bg-card p-3 font-mono text-sm"
        />
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="pb-1 text-[0.8125rem] font-bold text-[color:var(--text-secondary)]">
            Put the symbols in
          </legend>
          <label className="flex min-h-12 items-center gap-3">
            <input
              type="radio"
              name="import-into"
              checked={into === 'new'}
              onChange={() => {
                setInto('new')
                stale()
              }}
              className="size-5"
            />
            A new list
          </label>
          {into === 'new' && (
            <input
              aria-label="New list name"
              value={name}
              maxLength={MAX_NAME_LENGTH}
              onChange={(event) => {
                setName(event.target.value)
                stale() // review L11: a preview names the list it was read for; read it again
              }}
              className="h-12 rounded-[0.875rem] border border-input bg-card px-3 text-base"
            />
          )}
          {here && (
            <label className="flex min-h-12 items-center gap-3">
              <input
                type="radio"
                name="import-into"
                checked={into === 'here'}
                onChange={() => {
                  setInto('here')
                  stale()
                }}
                className="size-5"
              />
              This list ({here.name})
            </label>
          )}
        </fieldset>
        <button
          type="button"
          className={SECONDARY}
          disabled={busy || !text.trim()}
          onClick={() => void runPreview(text)}
        >
          {busy && !preview ? 'Reading' : 'Preview'}
        </button>
        {preview && (
          <ul aria-label="Preview" className="flex flex-col gap-2 text-sm leading-snug">
            {preview.words.map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ul>
        )}
        {error && (
          <p role="alert" className="text-sm font-medium text-[color:var(--down)]">
            {error}
          </p>
        )}
        <button
          type="button"
          className={PRIMARY}
          disabled={!preview || matched === 0 || busy || needsName}
          onClick={() => void doImport()}
        >
          Import {matched} symbols
        </button>
      </div>
    </BottomSheet>
  )
}
